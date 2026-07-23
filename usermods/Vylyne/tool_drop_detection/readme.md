# Feature

- Enables you to read 'live' values from the accelerometers
- Allows you to run templates similarly to the `[tool_probe_endstop]` `crash_gcode` on specific events.
- You have good accelerometers! Why not use them?

## Background

currently there is no actual way to obtain any accel values inside of macros. query prints to console, and the other dump to files.


## Installation

```bash
cd usermods/Vylyne/tool_drop_detection
./install.sh
```

Symlinks `tool_drop_detection.py` and `dock_autotune.py` into `klippy/extras/`, and symlinks the example `.cfg` files into `~/printer_data/config/tool_drop_detection/`. It does not touch `printer.cfg` for you -- add both includes yourself:

```
[include tool_drop_detection/tool_drop_detection.cfg]
[include tool_drop_detection/dock_autotune.cfg]
```

...then fill in `accelerometer:` (and per-tool `params_accel:`) for your printer -- see below. Override `KLIPPER_PATH` / `CONFIG_PATH` env vars if your install isn't at the defaults (`~/klipper`, `~/printer_data/config`). To remove everything: `./uninstall.sh` (leaves your `printer.cfg` includes for you to delete).

## How it works

![](./images/Anthead-domestic-abuse.gif)


## Status return
printer.tool_drop_detection.$NAME$.->

 - 'current':  { 'magnitude': 0, 'vector': {'x': 0, 'y': 0, 'z': 0}, 'rotation':  {'pitch': 0, 'roll': 0, 'vector': 0} }
   - *always filled with the latest values. (relative to `defaults_$NAME$`)*

- 'default':  {'base_g':  base_pitch': 0, 'base_roll': 0, 'base_vector': {'x': 0, 'y': 0, 'z': 0} }
  - *loaded from cfg or set with `TDD_REFRENCE_SET`*
   
- 'session':  {'peak': 0, 'magnitude': 0, 'rotation': {'pitch': 0, 'roll': 0, 'vector': 0}
  - *rolling avreage over 1s, peak is highest ever. `TDD_POLLING_RESET` to reset session and peak. (relative to `defaults_$NAME$`)*

 
 

## **[tool_drop_detection]**
- accelerometer: [comma seperated names]
- polling_freq: [1-20] (default: 1) -> *the frequency at which at ask the mcu for values*
- polling_rate: [see adxl345] (default: 50) -> *the frequency the accelerometer is spitting out values to mcu*

### crash/drop detection
- peak_g_threshold: [ 0.1 - 50 ] (default: 5) -> *if defined enables also triggering crash gcode when a peak higher than this is detected (instant)*
- rotation_threshold (vector <-> vector)
  - either:  rotation_threshold: [0.0 - 180.0] (±abs, vector to vector)
  - or:      pitch_threshold and/or roll_threshold: [0.0 - 180.0] (±abs, actual rotation angles)
- crash_mintime: [ 0.0 - 100.0 ] (default 1.0) -> *how long the angle has to be exceeded to be considered dropped* (high g events remain instant)
- crash_gcode: gcode template to be executed when THRESHOLD exceeded. provided with extra context: [ie: 'ACCEL':T1]

### angle templates (always use config set limits)
- angle_exceed: gcode template to be ran when the angle gets exceeded
- angle_return: gcode template to be ran when the angle returns to normal.
- hysterisis: [0.0-180.0] (default: 5.0) Hysterisis between those two templates executing.


- current_samples: [0-?] (default: 5) -> how many of the latest samples to use for updating the "current" key.
- session_time: [0.01-60] (default: 1) -> How long the session avreaging is. (in seconds)
- sample_results: [ median | average ] (default: median) -> *norm* (the way to calculate our current accel.)
- decimals: [ 0 - 10 ] (default: 3) -> *the amount of decimals to pack into our objects*

## commands *(all can take an optional [ACCEL] param, none for all)*
 * `TDD_POLLING_START` -> Starts the actual data gathering. [FREQ] [RATE] provided to overwrite internal settings.
 * `TDD_POLLING_STOP` -> Stops the actual data gathering.
 * `TDD_POLLING_RESET` -> Resets the current session. 
 * `TDD_QUERY` -> query (crashes when not polling, will fix when ive got time (or you fix it :P)) 
 * `TDD_REFERENCE_DUMP` -> dumps the current refrence frame `default_$NAME$: [g:$.$$  p:$.$$°  r:$.$$°  vec:($.$$$,$.$$$,$.$$$)]` to be added to config for defaults.
 * `TDD_REFERENCE_SET` -> Sets the current position as baseline 
 * `TDD_REFERENCE_RESET` -> Resets the current reference frame back to config defaults. 
 * `TDD_START` ([LIMIT_PITCH=0.0-180.0] [LIMIT_ROLL=0.0-180.0]) or [LIMIT_ANGLE=0.0-180.0] [/CRASH_MINTIME=0.0-100.0/] [/LIMIT_G=0.0-25/]
 * `TDD_STOP`  stops tool drop detection for that tool or all


## **[dock_autotune]**

Walks the *currently active* tool's `params_park_x` / `params_park_y` in small steps and keeps whichever offset minimises peak-g, using `tool_drop_detection` to measure it. Needs `tool_drop_detection` already configured with an accelerometer for the tool being tuned (via `accelerometer:` and/or the tool's `params_accel`).

- range_mm: (default: 1.0) -> ± search window, mm
- step_mm: (default: 0.1) -> mm increment
- pitch_tol / roll_tol: (default: 45) -> ° vs the dock's own baseline tilt, tripping the pause prompt
- threshold: (default: 0.1) -> peak-g increase (vs the best found so far) still considered "close enough" to keep walking
- changes_per: (default: 5) -> dock/undock cycles averaged per candidate position
- abort_on_g: (default: 15) -> peak-g that always pauses, no recheck
- guard_recheck_count / guard_recheck_delay: (default: 2 / 0.3) -> a tilt trip gets rechecked this many times, this many seconds apart, but *only* when the toolchanger's own tool-presence sensor confirms the tool is actually seated. A high-g trip is never auto-rechecked -- see below.
- toolchanger: (default: `toolchanger`) -> name of the `[toolchanger]` section to drive
- poll_freq / poll_rate: (default: 5 / 1600) -> accelerometer polling frequency/rate during the run
- baseline_settle / measure_settle: (default: 1.0 / 0.25) -> seconds to settle after undocking / after each pickup, before trusting a reading
- debug: (default: False)

### why it pauses, and what Retry does

A trip (tilt or high-g) shows a prompt with **Retry** / **Abort**. Retry always re-attempts the actual measurement -- a real undock/redock, not just a re-read -- so a transient deceleration spike usually clears on its own. For tilt specifically, if the toolchanger reports the tool as detected/present, the extra rereads it automatically first (`guard_recheck_*`) and only bothers you if it's still tilted after that; a high-g trip skips that auto-recheck since the peak reading is shared with the in-progress measurement and only a real redock (i.e. Retry) can trustworthily refresh it.

## commands
 * `TC_DOCK_AUTOTUNE` -> Start autotuning park_x/park_y for the currently active tool. Shows a baseline-tilt confirmation prompt (Record new / Continue), then walks X then Y.
 * `TC_DOCK_AUTOTUNE_CANCEL` -> Abort an in-progress run and restore the tool's original park position, path speed, pickup path and `verify_tool_pickup`.


