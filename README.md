# aop-presence

**BLUF:** A Python package and GUI that turns a TI AWR6843AOPEVM into an object detector. It reports whether something is in front of the sensor, how far away it is, and how big it is. It stays silent when the space is empty.

Runs against real hardware or a built-in simulator, so you can evaluate the GUI before the EVM arrives.

![status](https://img.shields.io/badge/tests-57%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## Quickstart

```bash
git clone <your-repo-url> && cd aop-presence
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Try it with no hardware attached:

```bash
aop-presence --simulate
```

Run it against the EVM:

```bash
aop-presence --radar-cfg configs/aop_presence_10fps.cfg
```

Ports autodetect via the CP2105 bridge. Override them when autodetect guesses wrong:

```bash
aop-presence --cli-port /dev/ttyUSB0 --data-port /dev/ttyUSB1     # Linux
aop-presence --cli-port COM4 --data-port COM5                     # Windows
```

On Linux, add yourself to the `dialout` group first, then log out and back in:

```bash
sudo usermod -aG dialout $USER
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

A target must clear all four to be reported. The GUI shows `NO OBJECT` until then, and `report.targets` is empty.

Stage 4 matters most and is the one people skip. A single frame containing a cluster is not presence. The default requires 3 consecutive frames to latch on and 6 to latch off. At 10 Hz that costs 300 ms of latency and buys a large drop in false alarms.

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
UART bytes -> FrameAssembler -> RadarFrame -> DetectionPipeline -> DetectionReport -> GUI
              (parser.py)                     (gate/cluster/size/hysteresis)
```

| Module | Responsibility |
|--------|---------------|
| `protocol.py` | Wire constants and the exception hierarchy |
| `parser.py` | Magic-word sync, header and TLV decode, resync after corruption |
| `types.py` | Every frozen dataclass crossing a module boundary |
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

- No multi-frame tracker. Targets are clustered per frame and associated only by "nearest is primary". Two people crossing paths will swap identity. Add a Kalman or GTRACK stage if you need persistent IDs.
- `clutterRemoval` is off, so a perfectly still target stays visible but static furniture also produces returns. Turn it on if you only care about motion.
- The `compRangeBiasAndRxChanPhase` values in the shipped `.cfg` are placeholders. Run TI's range bias calibration against a corner reflector for accurate absolute range.
- Tested against SDK 3.5 and 3.6 frame formats. The 4.x and MMWAVE-L-SDK demos changed TLV layouts and need a different parser.

## License

MIT. See `LICENSE`.
