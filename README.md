# CapeDar

Presence, ranging, and multi-object detection for the TI AWR6843AOPEVM mmWave
radar, indoors, outdoors, and airborne on a sUAS. Installs as `aop-presence`,
runs as `capedar`.

**BLUF:** A Python package, GUI, and SSH-friendly headless monitor that turns a
TI AWR6843AOPEVM into an object detector. It reports whether something is in
front of the sensor, how far away it is, and how big it is. It raises a
Raspberry Pi GPIO line immediately for multiple objects, or after one object
persists for three frames. It stays silent when the space is empty.

Runs against real hardware or a built-in simulator, so you can evaluate everything before the EVM arrives. `capedar` takes no required arguments.

![status](https://img.shields.io/badge/tests-193%20passing-brightgreen)
![coverage](https://img.shields.io/badge/coverage-74%25-green)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## Quickstart

```bash
git clone https://github.com/MatthewK84/CapeDar.git
cd CapeDar
python -m venv .venv
```

Activate the environment on Linux or macOS:

```bash
source .venv/bin/activate
```

Or activate it from Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

For the desktop GUI and development tools, install from the repository root:

```bash
python -m pip install -e ".[dev]"
```

For a headless Raspberry Pi, install only the lightweight core package:

```bash
python -m pip install -e .
```

Try it with no hardware attached. The `pair` scene walks a second object into
the room so you can watch immediate multi-object confirmation:

```bash
capedar --simulate --scenario pair
```

Run it against the EVM. Nothing else is required:

```bash
capedar
```

That autodetects the ports, attaches to the sensor if it is already streaming,
pushes the bundled profile if it is not, opens the GPIO line if the machine has
one, and prints to stdout. Every flag narrows that default; none of them enables it.

Headless is the default. It prints `DETECTED` / `CLEARED` for the detection and
GPIO transitions, `MULTI` / `MULTI-CLEARED` for occupancy transitions, and one
`STATUS` heartbeat per second. Stop it cleanly with `Ctrl+C`. Add `--gui` for
the Qt window.

Emit one JSON record per frame instead, for piping into something else. Logs go
to stderr, so stdout stays clean:

```bash
capedar --json | jq -r 'select(.multi_target) | .frame'
```

Ports autodetect via the CP2105 bridge. Override them when autodetect guesses wrong:

```bash
capedar --cli-port /dev/ttyUSB0 --data-port /dev/ttyUSB1     # Linux
capedar --cli-port COM4 --data-port COM5                     # Windows PowerShell
```

On Linux, add yourself to the `dialout` group first, then log out and back in:

```bash
sudo usermod -aG dialout $USER
```

## Detection signal line

The line goes HIGH when either:

- two resolvably distinct objects appear in one frame; or
- one object survives the detection gates for three consecutive frames.

It remains HIGH while presence is latched, then goes LOW after the configured
number of clear frames. With the Pi gates at 20 Hz, a single object confirms in
150 ms and clears after 300 ms without a qualifying return.

### Wiring, Raspberry Pi 5

Physical (BOARD) pin numbers, counted on the 40-pin header:

| Pi 5 pin | Function | Connect to |
|----------|----------|------------|
| 1  | 3V3 power | Module VCC |
| 9  | Ground    | Module GND |
| 11 | GPIO17    | Module signal input |

Install the GPIO backend on the Pi:

```bash
python -m pip install -e ".[pi]"
```

Then run it. `--gpio on` refuses to start if the pin cannot be opened, which is
what you want in the field; a silently dead signal line is worse than a refusal:

```bash
capedar --gpio on
```

### Pi 5 specifics that will bite you

- **RPi.GPIO does not work on the Pi 5.** The Pi 5 moved its GPIO behind the RP1 southbridge. pigpio does not support it either, so network remote-GPIO is off the table. This package uses gpiozero backed by lgpio, which is the supported Pi 5 path. gpiozero also resolves the gpiochip number itself, which matters because that number moved between Pi OS releases.
- **GPIO17 sources 16 mA.** Drive an opto-isolated or transistor module. Do not hang a relay coil or a bare LED off the pin.
- **3V3 logic.** Most 5V relay boards will not reliably switch on a 3.3V input. Check your module's datasheet before blaming the radar.
- **Invert if needed.** Modules that trigger on a low input want `--gpio-active-low`.

### Fail-safe behaviour

The line is driven LOW on startup, on shutdown, on `Ctrl+C`, on `SIGTERM`, on
any error, and when the radar stops sending frames for `--stale-timeout`
seconds (default 2.0). A line still asserted after the sensor died would be a
lie about the world, so every exit path de-asserts.

### Why counting clusters is not counting people

Cluster count is not object count, and code that pretends otherwise chatters.
Two failure modes dominate:

- **Fragmentation.** One person returns detections from torso, arms, and head. Density gaps split those into two or three clusters. Naive counting reports a crowd where one person stands. This package folds clusters closer together than `--min-separation` (default 0.75 m) into their strongest member, measuring separation in the ground plane so head and feet returns collapse correctly.
- **Merging.** Two people inside one azimuth cell return one cluster. This is a physical limit of a 4Rx/3Tx array and no post-processing recovers it.

The azimuth beam is roughly 15 degrees wide, so the closest two objects you can
resolve grows with range:

| Range | Minimum resolvable separation |
|-------|-------------------------------|
| 2 m   | ~0.5 m |
| 4 m   | ~1.0 m |
| 6 m   | ~1.6 m |
| 8 m   | ~2.1 m |

Past about 6 m, treat the multi-object signal as advisory. If one person reads
as two, raise `--min-separation`. If two close people read as one, they are
inside the same beam and the fix is to move the sensor, not the software.

Occupancy runs its own hysteresis. The field configuration confirms multiple
objects in one frame, allowing stronger evidence to bypass the three-frame
single-object delay. The ordinary presence clear latch still prevents the
physical line from chattering.

## Airborne operation on a sUAS

**BLUF: the indoor profile cannot be flown.** It measures plus or minus 0.97 m/s
of Doppler, which is slower than the aircraft carrying it, and its bundled
profile clamps the sensor Doppler gate to plus or minus 1.0 m/s. Every return
falls outside that gate the moment the platform moves. Use the airborne preset:

```bash
capedar --preset airborne --agl 4 --pitch 25 --gpio on
```

That selects a retuned chirp and a matching detection config in one step.

### Sunlight and glare do not affect this sensor

A 60 GHz radar does not see light. Sunlight, glare, haze, smoke, and darkness
change nothing about propagation at this wavelength, which is a large part of
why radar is worth carrying instead of a camera. Oxygen absorption near 60 GHz
costs roughly 15 dB/km, or 0.08 dB over a 5 m path, which is nothing.

Sunlight is still a real risk, just a thermal one. An airframe in direct sun
bakes the sensor, RF performance drifts with die temperature, and the part
eventually faults. The package now parses the temperature TLV when the firmware
emits it and warns as the die approaches its rated limit. Mount the EVM with a
thermal path to airframe metal and keep the radome out of direct sun where the
airframe allows it.

### What actually breaks when the sensor leaves the ground

| Problem | Effect | Handled by |
|---|---|---|
| Ego-motion | Every static object acquires the platform's Doppler; Doppler stops separating movers from clutter | `airborne.py`, fitted from the static field or supplied via `--ego-speed` |
| Doppler gate | Bundled profile clamps to plus or minus 1.0 m/s and discards everything in flight | `airborne_5m.cfg`, gate opened to plus or minus 13 m/s |
| Velocity ambiguity | Chirp only measures plus or minus 0.97 m/s, so platform motion aliases | `airborne_5m.cfg`, 31 us chirp period raises this to plus or minus 13.21 m/s |
| Ground clutter | Any downward tilt fills the range gates with ground | `--agl` and `--pitch` enable ground-plane rejection |
| Vibration | Rotor noise spreads Doppler around every return | Spatial clustering plus three-frame confirmation for a single target |
| Solar heating | Die temperature drift and eventual fault | Temperature TLV parsing and threshold warnings |

### The two chirp profiles

| | `default.cfg` (indoor) | `airborne_5m.cfg` |
|---|---|---|
| Range resolution | 0.044 m | 0.125 m |
| Max unambiguous range | 11.2 m | 7.97 m |
| **Max velocity** | **0.97 m/s** | **13.21 m/s** |
| Velocity resolution | 0.121 m/s | 0.206 m/s |
| Frame rate | 10 Hz | 20 Hz |
| Doppler FOV gate | plus or minus 1.0 m/s | plus or minus 13.0 m/s |

The airborne profile trades range resolution for velocity coverage and frame
rate. That trade is nearly free here: two objects at 5 m are limited to 1.32 m
separation by the 15 degree azimuth beam, not by range resolution, so 12.5 cm
range cells cost nothing that the antenna was going to deliver anyway.

### Multiple objects at 5 m

Resolvable separation is set by the azimuth beam, so it grows with range:

| Range | Minimum resolvable separation |
|---|---|
| 2 m | 0.53 m |
| 3 m | 0.79 m |
| 4 m | 1.05 m |
| 5 m | 1.32 m |

The airborne preset sets `min_target_separation_m` to 1.35 m to match. Two
objects closer than that at 5 m return one cluster and are reported as one
object. That is the antenna, not the software, and no post-processing recovers
it. Closer than about 3 m the sensor separates people standing side by side.

### Ego-motion, supplied or fitted

Static returns obey `doppler = -v * cos(bearing)`. Fitting that cosine across
the cloud recovers platform speed with no autopilot feed, which is what the
preset does by default. The fit is refused, rather than guessed, when there are
fewer than six points or too little bearing spread, because a cloud dominated by
one mover will happily fit a speed that is not real.

Prefer telemetry when you have it:

```bash
capedar --preset airborne --ego-speed 6.5 --agl 4 --pitch 25
```

Add `--movers-only 1.0` to reject anything moving with the static world. That
buys strong clutter rejection and gives up static targets, so a parked vehicle
disappears along with the ground it sits on.

### Before you fly this

The chirp parameters are derived from the SDK chirp equations and checked
against them in CI, but they have not been run on your unit. Load
`configs/airborne_5m.cfg` in the TI mmWave Demo Visualizer and confirm the
reported max range and max velocity first. Then bench the detection end of it
at 5 m on the ground before adding altitude.

## Running as a service

The repository includes a systemd unit and installer for Ubuntu on Raspberry
Pi 5. The service uses the field configuration established above:

- CLI `/dev/ttyUSB0`, data `/dev/ttyUSB1`
- `configs/aop_presence_10fps.cfg`
- `configs/detection_gates_pi.json`
- `--configure always`, so every service start pushes the complete profile
- `--gpio on`, so a GPIO problem fails loudly instead of silently disabling the LED

On the Pi, from the CapeDar checkout:

```bash
sudo apt update
sudo apt install python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[pi]"
sudo ./deploy/install-service.sh ubuntu
```

Replace `ubuntu` with the non-root account that owns the checkout if needed.
When invoked through `sudo`, omitting the name uses the invoking account. The
installer adds that account to `dialout` and, when present, `gpio`; renders the
unit with absolute paths to the current checkout; enables it for
`multi-user.target`; and starts it immediately. Do not move the checkout after
installation without reinstalling the unit.

Check status and follow the live terminal output through the journal:

```bash
sudo systemctl status capedar.service
sudo journalctl -u capedar.service -f
```

After editing a radar profile, gate file, or Python source in an editable
installation, restart the service:

```bash
sudo systemctl restart capedar.service
```

The unit restarts five seconds after a startup, serial, or GPIO failure. It
handles `SIGTERM` normally, drives the LED low, and does not restart after an
intentional `systemctl stop`.

To disable and remove the service:

```bash
sudo systemctl disable --now capedar.service
sudo rm /etc/systemd/system/capedar.service
sudo systemctl daemon-reload
```

## Hardware prerequisites

1. Flash the EVM with the Out-of-Box demo binary from mmWave SDK 3.5 or 3.6 (`xwr68xx_mmw_demo.bin`). Use UniFlash with SOP2 set for flashing mode, then clear SOP2 and reboot.
2. Confirm the board enumerates two serial ports. The lower-numbered port is CLI at 115200. The higher is data at 921600.
3. Verify the board works in the TI mmWave Demo Visualizer before debugging this tool. That separates board problems from host problems.

## How it avoids phantom detections

Your requirement was that the sensor stays quiet when nothing is there. A CFAR detector will always emit some points, so silence is engineered in four stages. Each stage removes a different failure mode.

| Stage | Where | Removes |
|-------|-------|---------|
| 1. On-sensor FOV and CFAR thresholds | `configs/*.cfg` | Detections outside the range and angle of interest, before they reach the host |
| 2. Per-point gating | `filters.py` | Weak returns, points behind the antenna, points outside the forward wedge |
| 3. Density clustering | `clustering.py` | Isolated points with no neighbours, which is what noise looks like |
| 4. Temporal hysteresis | `presence.py` | One-frame flashes, and one-frame dropouts when a person holds still |
| 5. Separation gate | `multitarget.py` | Fragments of one body counted as a second object |

A target must clear all four to be reported. The GUI shows `NO OBJECT` until then, and `report.targets` is empty.

Stage 4 matters most and is the one people skip. A single frame containing a cluster is not presence. The default requires 3 consecutive frames to latch on and 6 to latch off. At 10 Hz that costs 300 ms of latency and buys a large drop in false alarms.

## Presets and gate files

Three presets ship. `--preset` picks both the detection gates and the chirp
profile pushed to the sensor, so the two cannot drift apart.

| Preset | Range | Cluster eps | Min points | Confirm | Chirp profile | Source |
|---|---|---|---|---|---|---|
| `indoor` (default) | 8 m | 0.35 m | 3 | 3 | `default.cfg` | Derived, bench checked |
| `outdoor` | 8 m | 0.25 m | 2 | 3 | `default.cfg` | Tuned on hardware in live testing |
| `airborne` | 5 m | 0.50 m | 3 | 3 | `airborne_5m.cfg` | Derived, **not yet hardware validated** |

```bash
capedar --preset outdoor
capedar --preset airborne --agl 4 --pitch 25 --gpio on
```

The equivalent JSON gate files under `configs/` exist for the GUI and for
`--detection-cfg`:

| File | What it is |
|---|---|
| `detection_gates.json` | The `outdoor` preset. Live-tuned, the sane default for field work |
| `detection_gates_pi.json` | High-sensitivity Pi gates: one-point clusters, three-frame single confirmation, immediate multiple confirmation |
| `detection_gates.example.json` | Every field written out, as documentation |
| `detection_gates_debug.json` | Maximum sensitivity for bench work. Sets `cluster_min_points` and `frames_to_confirm` to 1, which **disables both stages that suppress single-point phantoms**. Do not field this |

A test asserts that every shipped gate file except the debug one keeps both
anti-phantom stages on, so this cannot regress by accident.

## Tuning

Every gate is live-adjustable in the GUI's **Detection gates** panel. Once you find values that work, save them:

```json
{
  "min_snr_db": 14.0,
  "max_range_m": 6.0,
  "max_azimuth_deg": 45.0,
  "cluster_eps_m": 0.35,
  "cluster_min_points": 4,
  "frames_to_confirm": 3,
  "frames_to_clear": 6
}
```

```bash
aop-presence --detection-cfg my_gates.json
```

Start here when tuning:

- **Phantom detections in an empty room?** Raise `min_snr_db` first, then `cluster_min_points`, then `frames_to_confirm`. Raising the CFAR threshold in the `.cfg` (8th argument of `cfarCfg`, in 0.25 dB steps) is the cheapest fix because it never reaches the UART.
- **Target dropping out?** Lower `min_snr_db`, raise `cluster_eps_m`, raise `frames_to_clear`.
- **One person read as two objects?** Raise `cluster_eps_m` to about 0.5 m.
- **Reflections off a back wall?** Lower `max_range_m` and tighten `cfarFovCfg` in the `.cfg`.

## Read the size number carefully

The width and height figures are **lower bounds, not measurements**.

A 4Rx/3Tx array has roughly 15 degrees of azimuth resolution. At 4 m, one resolution cell is about 1 m wide. Anything narrower than that cell measures as one cell wide. The GUI flags this with a `resolution-limited` note, and `TargetSize.resolution_limited` exposes it in code.

What this means in practice:

- Range depth is trustworthy. Range resolution is 4.4 cm with the shipped profile.
- Cross-range size separates "person" from "wall" reliably. It will not separate "person" from "coat rack".
- Do not build classification on the width number alone. Use range extent, point count, and Doppler together.

This is a physics limit of the array, not a software defect.

## Architecture

```
UART bytes -> FrameAssembler -> RadarFrame -> DetectionPipeline -> DetectionReport -> GUI/headless
              (parser.py)                     (gate/cluster/size/hysteresis)
```

| Module | Responsibility |
|--------|---------------|
| `protocol.py` | Wire constants and the exception hierarchy |
| `parser.py` | Magic-word sync, header and TLV decode, resync after corruption |
| `custom_types.py` | Every frozen dataclass crossing a module boundary |
| `config.py` | `DetectionConfig`, validated at construction |
| `filters.py` | Per-point SNR, range, and FOV gates |
| `clustering.py` | Iterative DBSCAN with an explicit queue, no recursion |
| `sizing.py` | Extent estimation, floored at the resolution cell |
| `presence.py` | Confirm/clear hysteresis state machine |
| `pipeline.py` | Composes the above into one `process(frame)` call |
| `sensor.py` | Serial link, config push, port autodetect |
| `simulator.py` | Byte-exact packet encoder and synthetic target source |
| `worker.py` | `QThread` that keeps serial reads off the event loop |
| `gui.py`, `plotview.py` | Qt window and the bird's-eye plot |
| `headless.py` | SSH-friendly detection events and status heartbeat |

The library has no Qt dependency below `worker.py`. Import `DetectionPipeline` and use it headless in a service.

## Library use

```python
from aop_presence import DetectionConfig, DetectionPipeline, RadarSensor

pipeline = DetectionPipeline(DetectionConfig(max_range_m=6.0, min_snr_db=14.0))

with RadarSensor("/dev/ttyUSB0", "/dev/ttyUSB1") as sensor:
    sensor.configure(Path("configs/aop_presence_10fps.cfg"))
    for frame in sensor.frames():
        report = pipeline.process(frame)
        target = report.primary
        if target is not None:
            print(f"{target.range_m:.2f} m at {target.azimuth_deg:+.1f} deg")
```

## Protocol reference

Frames follow the mmWave SDK Out-of-Box demo format:

- Magic word `02 01 04 03 06 05 08 07`, then a 40-byte header
- Each TLV is an 8-byte type/length pair plus payload. Length counts the payload only
- TLV 1 carries detected points, 16 bytes each: x, y, z, doppler as float32
- TLV 7 carries side info, 4 bytes each: SNR and noise as int16 in 0.1 dB units
- The packet is zero-padded to a multiple of 32 bytes

`guiMonitor` in the shipped profile enables only TLV 1 and TLV 7. Heatmaps are large and unused here, and dropping them protects frame rate on the 921600 baud link.

Axes use the TI convention: +x right, +y downrange (boresight), +z up.

## Development

```bash
ruff check . && ruff format .
mypy
pytest
```

CI runs all three on 3.10 through 3.12. The code targets the strict standards in this repo: full type hints, no recursion, functions under 30 lines, no bare excepts, no global mutable state.

## Known limitations

- The airborne chirp profile is derived and unit-tested against the SDK equations, but has not been validated on hardware. Confirm it in the mmWave Demo Visualizer before flight.
- Ego-motion compensation models forward flight only. Sideslip, climb, and yaw rate are not modelled, and a hard yaw will degrade the fit until it is refused.
- Ground rejection assumes flat ground at a known height. Sloping terrain biases the computed height and will either leak ground returns or clip low targets.
- Two objects inside one azimuth beam return one cluster. At 6 m that is anything closer than ~1.6 m together. This is physics, not a bug, and no amount of software recovers it. Move the sensor closer or accept the limit.
- The multi-object signal counts resolvably distinct scattering centres, not people. A person pushing a cart may read as two objects; two people hugging read as one.

- No multi-frame tracker. Targets are clustered per frame and associated only by "nearest is primary". Two people crossing paths will swap identity. Add a Kalman or GTRACK stage if you need persistent IDs.
- `clutterRemoval` is off, so a perfectly still target stays visible but static furniture also produces returns. Turn it on if you only care about motion.
- The `compRangeBiasAndRxChanPhase` values in the shipped `.cfg` are placeholders. Run TI's range bias calibration against a corner reflector for accurate absolute range.
- Tested against SDK 3.5 and 3.6 frame formats. The 4.x and MMWAVE-L-SDK demos changed TLV layouts and need a different parser.

### Quick Example

```bash
aop-presence --cli-port /dev/ttyUSB0 --data-port /dev/ttyUSB1 --radar-cfg recommended_1.cfg --detection-cfg detection_gates.json --gpio
```

## License

MIT. See `LICENSE`.
