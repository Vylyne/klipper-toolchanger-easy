# Tool-dock parking-position autotune.
#
# Walks each tool's params_park_x / params_park_y in small steps and keeps
# whichever offset minimises peak-g measured by tool_drop_detection. The
# toolchanger's own pickup-verification interlocks (verify_tool_pickup, the
# pickup path's per-step 'verify') exist to catch a bad dock -- which is
# exactly what a deliberate mis-dock candidate looks like -- so they are
# suspended for the duration of the run only, and always restored, instead
# of requiring a permanent printer.cfg change.

import copy

PHASE_IDLE = 'idle'
PHASE_AWAITING_ACCEPT = 'awaiting_baseline_accept'
PHASE_BUSY = 'busy'
PHASE_PAUSED = 'paused'


class DockAutotune:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        self.toolchanger_name = config.get('toolchanger', 'toolchanger')
        self.range_mm = config.getfloat('range_mm', 1.0, above=0.)
        self.step_mm = config.getfloat('step_mm', 0.1, above=0.)
        self.pitch_tol = config.getfloat('pitch_tol', 45., minval=0.)
        self.roll_tol = config.getfloat('roll_tol', 45., minval=0.)
        self.threshold = config.getfloat('threshold', 0.1)
        self.changes_per = config.getint('changes_per', 5, minval=1)
        self.abort_on_g = config.getfloat('abort_on_g', 15., above=0.)
        self.poll_freq = config.getfloat('poll_freq', 5., above=0.)
        self.poll_rate = config.getint('poll_rate', 1600)
        self.baseline_settle = config.getfloat('baseline_settle', 1.0, minval=0.)
        self.measure_settle = config.getfloat('measure_settle', 0.25, minval=0.)
        self.guard_recheck_count = config.getint('guard_recheck_count', 2, minval=0)
        self.guard_recheck_delay = config.getfloat('guard_recheck_delay', 0.3, minval=0.)
        self.confirm_retries = config.getint('confirm_retries', 2, minval=0)
        self.debug = config.getboolean('debug', False)

        self.phase = PHASE_IDLE
        self.resume_phase = None
        self.tool = None
        self.short = None
        self.orig = {}
        self.best = {}
        self.walk = {}
        self.axis_idx = 0
        self.dir_idx = 0
        self.cur_step = 0.0
        self.best_peak = 0.0
        self.base_peak = 0.0
        self.dock_pitch = 0.0
        self.dock_roll = 0.0
        self.backoff_count = 0
        self._last_guard_info = {}

        self.printer.register_event_handler('klippy:connect', self._handle_connect)

        self.gcode.register_command(
            'TC_DOCK_AUTOTUNE', self.cmd_TC_DOCK_AUTOTUNE,
            desc=self.cmd_TC_DOCK_AUTOTUNE_help)
        self.gcode.register_command(
            'TC_DOCK_AUTOTUNE_CANCEL', self.cmd_TC_DOCK_AUTOTUNE_CANCEL,
            desc=self.cmd_TC_DOCK_AUTOTUNE_CANCEL_help)
        self.gcode.register_command('_TC_DOCK_AUTOTUNE_RESUME', self.cmd_RESUME)
        self.gcode.register_command(
            '_TC_DOCK_AUTOTUNE_REDO_BASELINE_BUTTON', self.cmd_REDO_BASELINE)
        self.gcode.register_command(
            '_TC_DOCK_AUTOTUNE_ABORT_BUTTON', self.cmd_TC_DOCK_AUTOTUNE_CANCEL)

    def _handle_connect(self):
        self.tc = self.printer.lookup_object(self.toolchanger_name)
        self.tdd = self.printer.lookup_object('tool_drop_detection')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.heaters = self.printer.lookup_object('heaters')
        self.reactor = self.printer.get_reactor()

    # ─── small helpers ──────────────────────────────────────────────────
    def _prompt(self, *lines):
        for line in lines:
            self.gcode.run_script_from_command('RESPOND TYPE=command MSG="%s"' % (line,))

    def _set_accel(self, value):
        self.gcode.run_script_from_command('SET_VELOCITY_LIMIT ACCEL=%.6f' % (value,))

    def _settle(self, seconds):
        self.toolhead.dwell(seconds)
        self.toolhead.wait_moves()

    # ─── entry point ──────────────────────────────────────────────────────
    cmd_TC_DOCK_AUTOTUNE_help = "Autotune park_x / park_y for the current tool by minimising peak-g"

    def cmd_TC_DOCK_AUTOTUNE(self, gcmd):
        if self.phase != PHASE_IDLE:
            gcmd.respond_info("Autotune already running (phase=%s)." % (self.phase,))
            return
        tool = self.tc.active_tool
        if tool is None:
            raise gcmd.error("No active tool equipped.")
        curtime = self.reactor.monotonic()
        if self.toolhead.get_status(curtime)['homed_axes'] != 'xyz':
            raise gcmd.error("Please home first.")

        accel_raw = tool.params.get('params_accel', tool.name.replace('tool ', ''))
        short = self.tdd.resolve(accel_raw)
        if short is None:
            raise gcmd.error(
                "dock_autotune: no tool_drop_detection accelerometer found for %s "
                "(params_accel=%s)" % (tool.name, accel_raw))

        self.tool = tool
        self.short = short
        self.phase = PHASE_BUSY
        self.backoff_count = 0

        # Snapshot everything we are about to touch so it can be restored
        # exactly, whether we finish, cancel, or the printer errors out.
        self.orig = {
            'px': float(tool.params['params_park_x']),
            'py': float(tool.params['params_park_y']),
            'path_speed': float(tool.params['params_path_speed']),
            'pickup_path': copy.deepcopy(tool.params['params_pickup_path']),
            'verify_tool_pickup': self.tc.verify_tool_pickup,
            'max_accel': self.toolhead.get_status(curtime)['max_accel'],
        }

        stripped_path = []
        for step in self.orig['pickup_path']:
            step = dict(step)
            step.pop('verify', None)
            stripped_path.append(step)
        tool.set_parameter('params_pickup_path', stripped_path)
        self.tc.verify_tool_pickup = False

        # The pickup path's own M109 (if any) waits for whatever the CURRENT
        # target is -- turned off here so every pickup in the scan sees
        # "wait for 0", a no-op, instead of repeatedly reheating/waiting for
        # a target (e.g. left on from a prior QGL/probing sequence) this run
        # has no use for. Not restored afterward: autotune doesn't run mid-
        # print, so there's nothing to resume back up to.
        heater = tool.extruder.get_heater() if tool.extruder else None
        if heater and heater.get_status(curtime)['target']:
            self.heaters.set_temperature(heater, 0.0, False)

        self.gcode.run_script_from_command('STOP_TOOL_PROBE_CRASH_DETECTION')
        self._set_accel(self.orig['max_accel'] / 5.0)

        self.tdd.start_polling([short], self.poll_freq, self.poll_rate)
        self.toolhead.wait_moves()
        self.tdd.reset_polling([short])

        gcmd.respond_info(
            "Dock autotune started, range: +/-%gmm and step: +/-%gmm" % (
                self.range_mm, self.step_mm))

        self._record_baseline(gcmd)

    # ─── baseline capture ─────────────────────────────────────────────────
    def _record_baseline(self, gcmd):
        gcmd.respond_info("Recording baseline, unselecting")
        self._set_accel(self.orig['max_accel'] / 10.0)
        self.tool.set_parameter('params_path_speed', self.orig['path_speed'] / 5.0)

        self.tc.select_tool(gcmd, None, "")
        self.toolhead.wait_moves()
        self.tdd.reset_polling([self.short])
        self._settle(self.baseline_settle)

        rot = self.tdd.rotation(self.short, 'session')
        self.dock_pitch = rot['pitch']
        self.dock_roll = rot['roll']

        self.tool.set_parameter('params_path_speed', self.orig['path_speed'])
        self._set_accel(self.orig['max_accel'])

        self.tc.select_tool(gcmd, self.tool, "")
        self.toolhead.wait_moves()

        self._prompt(
            "action:prompt_begin Dock alignment check",
            "action:prompt_text Current Tilt alignment:",
            "action:prompt_text f-b: %.3f deg" % (self.dock_pitch,),
            "action:prompt_text l-r: %.3f deg" % (self.dock_roll,),
            "action:prompt_button Record new|_TC_DOCK_AUTOTUNE_REDO_BASELINE_BUTTON|warning",
            "action:prompt_footer_button Continue|_TC_DOCK_AUTOTUNE_RESUME|success",
            "action:prompt_show",
        )
        self.phase = PHASE_AWAITING_ACCEPT

    def cmd_REDO_BASELINE(self, gcmd):
        if self.phase != PHASE_AWAITING_ACCEPT:
            return
        self.phase = PHASE_BUSY
        self._record_baseline(gcmd)

    # ─── measurement / hill-climb ───────────────────────────────────────
    def _measure(self, gcmd):
        """Dock/undock changes_per times, returning the averaged peak, or
        None if the guard tripped (after its own quick settle-reread)."""
        peaks = []
        for _ in range(self.changes_per):
            self.tc.select_tool(gcmd, None, "")
            self.tc.select_tool(gcmd, self.tool, "")
            self._settle(self.measure_settle)
            if not self._guard_ok(gcmd):
                return None
            peaks.append(self.tdd.peak(self.short))
            self.tdd.reset_polling([self.short])
        return round(sum(peaks) / len(peaks), 5)

    def _measure_confirmed(self, gcmd):
        """Run _measure at the current candidate, retrying (a fresh redock,
        not just a reread) up to confirm_retries more times if it trips --
        any attempt coming back clean is treated as the real reading and the
        earlier trip(s) as a one-off. Returns (avg_peak, tripped); tripped is
        only True once EVERY attempt has failed, at which point
        self._last_guard_info reflects the final attempt."""
        for attempt in range(1 + self.confirm_retries):
            avg_peak = self._measure(gcmd)
            if avg_peak is not None:
                return avg_peak, False
            if self.debug and attempt < self.confirm_retries:
                gcmd.respond_info(
                    "[dock_autotune] trip on attempt %d/%d, retrying to confirm" % (
                        attempt + 1, 1 + self.confirm_retries))
        return None, True

    def _pause(self, resume_phase):
        info = self._last_guard_info
        self.resume_phase = resume_phase
        self.phase = PHASE_PAUSED

        lines = [
            "action:prompt_begin Dock alignment issue",
            "action:prompt_text Pitch: %.2f deg, Roll: %.2f deg" % (info['pitch'], info['roll']),
            "action:prompt_text Peak: %.2f g" % (info['peak'],),
        ]
        if info['tool_present']:
            lines.append(
                "action:prompt_text Tool IS detected on shuttle - may be a transient spike")
        lines += [
            "action:prompt_button Retry|_TC_DOCK_AUTOTUNE_RESUME|warning",
            "action:prompt_footer_button Abort|TC_DOCK_AUTOTUNE_CANCEL|error",
            "action:prompt_show",
        ]
        self._prompt(*lines)

    def _begin_measurement(self, gcmd):
        self._set_accel(self.orig['max_accel'] / 2.0)
        self.tool.set_parameter('params_path_speed', self.orig['path_speed'] / 2.0)

        base_peak, tripped = self._measure_confirmed(gcmd)
        if tripped:
            # No direction has been seeded yet to back off from -- always
            # needs a human, regardless of tool_present.
            self._pause('baseline')
            return

        self.base_peak = base_peak
        self.best_peak = base_peak
        self.best = {'x': self.orig['px'], 'y': self.orig['py']}
        self.walk = dict(self.best)
        gcmd.respond_info(
            "Baseline: %.3fg, dock_pitch: %.2f deg, dock_roll: %.2f deg" % (
                base_peak, self.dock_pitch, self.dock_roll))

        self.axis_idx = 0
        self.dir_idx = 0
        self.cur_step = 0.0
        self._run_scan(gcmd)

    def _walk_direction(self, gcmd, axis, direction):
        """Step `axis` in `direction` while it keeps improving (or stays within
        threshold), snapping back to the true best once it regresses -- either
        because it got worse, or because a confirmed-persistent guard trip
        backed it off (the tool is still corroborated present, so continuing
        further this way is assumed to only get worse, not better). Returns
        False only when a trip can't be corroborated and needs a human;
        state is then left resumable."""
        step = self.step_mm * direction
        while abs(self.cur_step + step) <= self.range_mm:
            cand = round(self.walk[axis] + step, 3)
            self.tool.set_parameter('params_park_%s' % (axis,), cand)

            avg_peak, tripped = self._measure_confirmed(gcmd)
            if tripped:
                if not self._last_guard_info.get('tool_present'):
                    self._pause('scanning')
                    return False
                self.backoff_count += 1
                info = self._last_guard_info
                gcmd.respond_info(
                    "Guard tripped %d/%d attempts at this candidate (pitch=%.2f "
                    "roll=%.2f peak=%.2fg, tool detected on shuttle) -- backing "
                    "off %s%s." % (
                        1 + self.confirm_retries, 1 + self.confirm_retries,
                        info['pitch'], info['roll'], info['peak'],
                        axis, '+' if direction > 0 else '-'))
                self.tool.set_parameter('params_park_%s' % (axis,), round(self.best[axis], 3))
                self.walk[axis] = self.best[axis]
                self.toolhead.wait_moves()
                break

            delta = avg_peak - self.best_peak
            if delta <= self.threshold:
                self.walk[axis] = cand
                self.cur_step += step
                if avg_peak < self.best_peak:
                    self.best_peak = avg_peak
                    self.best[axis] = cand
            else:
                self.tool.set_parameter('params_park_%s' % (axis,), round(self.best[axis], 3))
                self.walk[axis] = self.best[axis]
                self.toolhead.wait_moves()
                break
        return True

    def _run_scan(self, gcmd):
        axes = ('x', 'y')
        dirs = (1, -1)
        while self.axis_idx < len(axes):
            axis = axes[self.axis_idx]
            while self.dir_idx < len(dirs):
                if not self._walk_direction(gcmd, axis, dirs[self.dir_idx]):
                    return  # guard tripped; axis_idx/dir_idx/cur_step left resumable
                self.dir_idx += 1
                self.cur_step = 0.0
            self.dir_idx = 0
            self.axis_idx += 1
        self._finish(gcmd)

    # ─── guard ──────────────────────────────────────────────────────────
    def _tool_present(self):
        """Best-effort corroboration that self.tool is the one actually
        mounted right now, via whichever presence mechanism is configured:
        a per-tool detection_pin (toolchanger.detected_tool), or -- since
        require_tool_present setups commonly use a shared tool-mounted probe
        instead -- tool_probe_endstop acting as a virtual presence sensor."""
        if self.tc.has_detection and self.tc.detected_tool is self.tool:
            return True
        probe = self.printer.lookup_object('probe', default=None)
        if probe is None or not hasattr(probe, 'active_tool_number'):
            return False
        self.gcode.run_script_from_command('DETECT_ACTIVE_TOOL_PROBE')
        status = probe.get_status(self.reactor.monotonic())
        return status.get('active_tool_number') == self.tool.tool_number

    def _guard_ok(self, gcmd):
        """One evaluation of current sensor state, including a quick
        settle-reread for a tilt trip (cheap, no redock). Returns True if
        fine. On failure, records self._last_guard_info and returns False --
        no prompt, no phase change; the caller (via _measure_confirmed)
        decides what a trip means."""
        rot = self.tdd.rotation(self.short, 'current')
        pitch, roll = rot['pitch'], rot['roll']
        peak = self.tdd.peak(self.short)
        tilting = abs(pitch) > self.pitch_tol or abs(roll) > self.roll_tol
        high_g = peak >= self.abort_on_g

        if not tilting and not high_g:
            return True

        # The toolchanger's own presence sensor is an independent signal from
        # the accelerometer. If it confirms the tool is seated, a tilt trip is
        # likely settling vibration right after the move rather than a real
        # drop, and it is cheap to reread. A high-g trip is NOT auto-rechecked
        # even when corroborated: session peak is max-hold and shared with the
        # in-flight measurement, so a trustworthy recheck needs a full
        # undock/redock, not just a reread -- _measure_confirmed's retry loop
        # is what provides that for high-g.
        tool_present = self._tool_present()

        if tilting and not high_g and tool_present:
            for _ in range(self.guard_recheck_count):
                self.reactor.pause(self.reactor.monotonic() + self.guard_recheck_delay)
                rot = self.tdd.rotation(self.short, 'current')
                pitch, roll = rot['pitch'], rot['roll']
                if abs(pitch) <= self.pitch_tol and abs(roll) <= self.roll_tol:
                    if self.debug:
                        gcmd.respond_info("[dock_autotune] tilt trip cleared on recheck")
                    return True

        self._last_guard_info = {
            'pitch': pitch, 'roll': roll, 'peak': peak, 'tool_present': tool_present,
        }
        return False

    def cmd_RESUME(self, gcmd):
        if self.phase == PHASE_AWAITING_ACCEPT:
            self.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
            self.phase = PHASE_BUSY
            self._begin_measurement(gcmd)
            return
        if self.phase == PHASE_PAUSED:
            self.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')
            resume_phase, self.resume_phase = self.resume_phase, None
            self.phase = PHASE_BUSY
            if resume_phase == 'baseline':
                self._begin_measurement(gcmd)
            else:
                self._run_scan(gcmd)

    # ─── completion / cancel ────────────────────────────────────────────
    def _finish(self, gcmd):
        self.tdd.stop_polling([self.short])
        self.tool.set_parameter('params_park_x', round(self.best['x'], 3))
        self.tool.set_parameter('params_park_y', round(self.best['y'], 3))
        self.tool.reset_parameter('params_path_speed')
        self.tool.reset_parameter('params_pickup_path')
        self.tc.verify_tool_pickup = self.orig['verify_tool_pickup']
        self.gcode.run_script_from_command('START_TOOL_PROBE_CRASH_DETECTION')
        self._set_accel(self.orig['max_accel'])

        dx = self.best['x'] - self.orig['px']
        dy = self.best['y'] - self.orig['py']
        gcmd.respond_info(
            "Done -> dX: %.3fmm, dY: %.3fmm old peak: %.3fg, new peak: %.3fg" % (
                dx, dy, self.base_peak, self.best_peak))
        gcmd.respond_info("params_park_x: %.3f" % (self.best['x'],))
        gcmd.respond_info("params_park_y: %.3f" % (self.best['y'],))
        if self.backoff_count:
            gcmd.respond_info(
                "%d guard trip(s) were auto-backed-off near the search range edge "
                "(see console log above for details)." % (self.backoff_count,))
        self.phase = PHASE_IDLE
        self.resume_phase = None

    cmd_TC_DOCK_AUTOTUNE_CANCEL_help = "Abort an in-progress TC_DOCK_AUTOTUNE run and restore original park position"

    def cmd_TC_DOCK_AUTOTUNE_CANCEL(self, gcmd):
        if self.phase == PHASE_IDLE:
            gcmd.respond_info("Dock autotune is not running.")
            return
        self.gcode.run_script_from_command('RESPOND TYPE=command MSG="action:prompt_end"')

        self.tdd.stop_polling([self.short])
        self.tool.reset_parameter('params_path_speed')
        self.tool.reset_parameter('params_pickup_path')
        self.tool.set_parameter('params_park_x', self.orig['px'])
        self.tool.set_parameter('params_park_y', self.orig['py'])
        self.tc.verify_tool_pickup = self.orig['verify_tool_pickup']
        self.gcode.run_script_from_command('START_TOOL_PROBE_CRASH_DETECTION')
        self._set_accel(self.orig['max_accel'])

        gcmd.respond_info(
            "Dock autotune aborted, park position restored to X:%.3f Y:%.3f" % (
                self.orig['px'], self.orig['py']))
        self.phase = PHASE_IDLE
        self.resume_phase = None


def load_config(config):
    return DockAutotune(config)
