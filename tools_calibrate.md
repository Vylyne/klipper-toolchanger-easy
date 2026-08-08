# Tools calibrate

An extension to calibrate tool offsets with a nozzle contact probe.
Works by bumping the nozzle into the probe form multiple angles to pinpoint the nozzle location.

See [Nozzle Align](https://github.com/viesturz/NozzleAlign) repo for some sensor ideas.

### Configuration

See the [example](/examples/calibrate-offsets.cfg) folder for a full example.
```
[tools_calibrate]

pin: GPIO pin (e.g., '^PG11')
     The pin Klipper will monitor to detect a probe trigger.
     - normally-open vs normally-closed depends on how the switch is wired
       (which COM/NO/NC lugs are used), not on the probe type as such - but
       both the sexbolt and nudge, when built on the standard Voron
       Z-endstop switch/PCB reference design, come out normally-closed,
       since that board's traces fix which lugs are used.
     - add inversion (ie: !PG11) if idle reads triggered, or drop it if idle
       reads open - verify with TOOL_CALIBRATE_QUERY_PROBE before relying on
       it (see "Detecting whether a removable probe is plugged in" below),
       especially if your probe isn't built on that reference board.

spread:               (mm)
    X/Y distance from center for probing sequence
    This defines how far the tool moves during the touch pattern.
    - For large pins (≥5mm), use 3.5-4.0 
    - Larger values = more overtravel, takes longer, safer for larger variance in tools or larger pins
    - Smaller values = less overtravel but may hit too early for large variance tools/large pins
    - Example: a 5mm pin, a 2.5mm spread would touch the pins face. (assuming nozzle = cylinder with 0 width)

lower_z:              (mm)
   Distance to lower the nozzle to hit. (0 -> slides over | 3-4 -> hits silicone sock)
   - 0.1-0.2 = minimal travel, may work, usually cleaner nozzle around here
   - 0.4-0.5 = safer hit margin, possibly less accurate.
   - Can be overridden per-call with `LOWER_Z=` on `TOOL_LOCATE_SENSOR` /
     `TOOL_CALIBRATE_TOOL_OFFSET`, e.g. to probe at several heights without
     editing the config each time (see `DIAGNOSE_TOOL_OFFSET_PRECISION` in
     `examples/easy-additions/calibrate-offsets.cfg`).

travel_speed:         (mm/s)
   Move speed between probes 
   - 0.1-infinity (really doesnt matter that much)

speed:                (mm/s)
   move speed during probes 
   - too slow -> takes forever | too fast -> not accurate enough
   - 0.5-10 would be an average/sane range

lift_speed:           (mm/s)
   speed with which to raise Z

final_lift_z:         (mm)
   Distance to raise Z between/after probing.
   Will also the the distance its waiting above the probe.

sample_retract_dist:  (mm)
   Z retract between samples (Z) 
   - too little -> backlash/doesnt untrigger | too much -> moves up too high/takes longer.
   - 0.2-5 

samples: 
    Number of probe samples to take (usually 3-5)

samples_tolerance:    (mm) 
     Max variance allowed between samples (will retry/abort if exceeded)
     a good probe will work with 0.05, altho increasing it has no effect on results.
     more a "sanity check" then anything else.

samples_tolerance_retries: 
     the amount of times to retry the probing when the sample tolerance has been exceeded.

samples_result:       ['median' | 'average']
     output result method 

pretrigger_retries: (default 2)
     If a probe move fails with "Probe triggered prior to movement", this is
     usually a transient blip (switch bounce, a marginal/intermittent
     connection) rather than the nozzle genuinely resting on the probe.
     After a short settle (`pretrigger_settle_time`), the endstop is
     re-queried: if it now reads clear, the probe move is retried instead of
     aborting the whole calibration. If it's still triggered after settling,
     it's treated as real and raised immediately - this never masks a
     genuine trigger, only a transient one. Set to 0 to disable and restore
     the old hard-fail-immediately behavior. Overridable per-call with
     `PRETRIGGER_RETRIES=`.

pretrigger_settle_time: (seconds, default 0.3)
     Dwell time before re-checking the endstop for `pretrigger_retries`.

trigger_to_bottom_z:  (mm)
    Used in trigger calibration calculations.
    Defines Z distance from calibration probe *trigger* to mechanical bottom out.
    sort of like the distance from when your keyboard key registers a hit, to where it actually hits the bottom.
    - 0-3 best calibrated by setting it to 0, 
      running TOOL_CALIBRATE_PROBE_OFFSET and substracting the result from your current probe offset.
    - decrease if the nozzle is too high, increase if too low.

probe: probe 
     (optional name of the nozzle probe to use)
```

### Clean your nozzles 

The calibration accuracy is as good as your nozzles are clean. 
Clean all nozzles thoroughly before calibrating.

### Calibrating tool offsets

- First position the nozzle approximately above the probe - the probe will find the center on it's own within 1-2 mm.

- The first tool has all offsets to 0 and is used as a baseline for other tools. Run ```TOOL_LOCATE_SENSOR``` to calibrate nozzle location for tool 0.

- For every other tool, run ```TOOL_CALIBRATE_TOOL_OFFSET``` to measure the offset from the first tool.

All probing moves and final offsets will be printed in the console.

### Diagnosing a suspicious tool offset

If a non-baseline tool's calibrated Z offset seems consistently wrong (not just noisy from run to
run), it helps to know whether you're looking at measurement noise or a real bias before changing
anything. `DIAGNOSE_TOOL_OFFSET_PRECISION` (`examples/easy-additions/calibrate-offsets.cfg`)
repeats the measurement several times at several `LOWER_Z` heights and prints the min/max/spread
for each height:

- Position and heat the tool as you would before `TOOL_LOCATE_SENSOR`/`TOOL_CALIBRATE_TOOL_OFFSET`.
- Run `DIAGNOSE_TOOL_OFFSET_PRECISION BASELINE=1` for tool 0, or
  `DIAGNOSE_TOOL_OFFSET_PRECISION` for any other tool (after tool 0's baseline has been captured).
  `HEIGHTS` and `REPEATS` are overridable, e.g. `HEIGHTS="0.1,0.5" REPEATS=8`.
- A small, flat spread across heights points at noise (sampling/speed/retries) or something outside
  this module entirely (nozzle cleanliness/wear). A spread that changes cleanly with height suggests
  the nozzle/pin contact geometry itself is height-dependent for that tool.
- Each run also reports probe retries per height and a retry/abort total (see
  `pretrigger_retries` above). Because these faults are intermittent, the retry *rate* is a much
  better signal than waiting for a hard failure: a change that takes you from 6 retries per run to
  1 is visible immediately, where a hard failure might not recur for several runs by luck alone.

The measurement loop lives in the `TOOL_CALIBRATE_DIAGNOSE` command rather than in the macro,
because a `gcode_macro` cannot do it: Klipper renders a macro template in full *before* executing
any of its commands, so a `printer.tools_calibrate.last_z_result` lookup inside a Jinja loop
returns the same pre-probe value on every iteration rather than the result of each probe.

### Counting intermittent probe faults

`TOOL_CALIBRATE_RESET_COUNTERS` zeroes the per-run counters, which are exposed for use in your own
macros as:

```
printer.tools_calibrate.pretrigger_retries_run     # recovered after settling, this run
printer.tools_calibrate.pretrigger_aborts_run      # still triggered after settling, this run
printer.tools_calibrate.pretrigger_retries_total   # since Klipper started
printer.tools_calibrate.pretrigger_aborts_total    # since Klipper started
```

The *ratio* is diagnostic. Retries that recover after a settle suggest a mechanical fault - the
probe not returning to its rest position quickly enough between touches. Aborts, where the endstop
is still triggered after settling, suggest a persistently open circuit - contact resistance, oxide
films on the contact surfaces, or a marginal connection somewhere in the wiring.

### Calibrating nozzle bed probe.

- Do the first two steps from above to ensure the probe is precisely under the nozzle.

- Run TOOL_CALIBRATE_PROBE_OFFSET - to measure Z offset from nozzle triggering the probe to tool's nozzle probe activating.

All probing moves and final offsets will be printed in the console.


## Troubleshooting

### Probe triggered prior to movement

As of `pretrigger_retries` (default 2), a single transient occurrence of this error no longer
aborts the whole calibration: the endstop is re-checked after a short settle, and if it's clear the
probe move is retried automatically (you'll see "...retrying probe (N/M)" in the console). If it's
still triggered after settling, or retries are exhausted, it's raised as before - so if you're
still seeing a hard failure, you're looking at a real or persistent fault, not a one-off blip. Work
through the checks below, and see "Diagnosing a suspicious tool offset" above for a repeatable way
to reproduce it. Frequent retries showing up in the console (even when they eventually succeed) are
themselves a signal of a marginal connection worth chasing down (loose crimp, worn contact, etc.)
before it gets bad enough to exhaust the retry budget.

- the nozzle is not touching the probe
  - Check if the probe is triggering without touching  
  - use a multimeter to check for continuity and if it changes when pressing down on the probe. (or use `TOOL_CALIBRATE_QUERY_PROBE` to query the status of the probe)
  - Check if the pin is configured correctly. The example configuration is for active low with a pullup. Depending how you have wired it, you might need remove the **^!** for active-high.
  
- the nozzle is touching the probe - (and could have probed a few times already)
  - Likely the initial position was too far off-center. Try to position it more accurately.
  - The probe is lowered too much and/or not enough sideways - tweak `lower_z` and `spread`

### Detecting whether a removable probe is plugged in

If your probe is a removable fixture rather than a permanently mounted one, you can use its
endstop state as a presence check from other macros (e.g. to raise clearance during QGL/homing
so you don't crash into it when it's plugged in). `TOOL_CALIBRATE_QUERY_PROBE` refreshes and
exposes the raw endstop state as `printer.tools_calibrate.calibration_probe`: `True` when
triggered, `False` when not triggered. Add `QUIET=1` to suppress its console message when calling
it from a macro.

For a normally-closed (NC) probe wired per the `pin:` guidance above (no `!` inversion), the
contacts are closed (continuity, not triggered) while idle and connected, and open (triggered)
both while being touched *and* when unplugged. So outside of an active calibration touch,
`calibration_probe == True` means the probe isn't plugged in: connected ⟺
`not printer.tools_calibrate.calibration_probe`. Verify this polarity on your hardware by running
`TOOL_CALIBRATE_QUERY_PROBE` with the probe unplugged and then plugged in (idle) — if it reads
backwards, invert the check in your macros.
