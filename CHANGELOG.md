# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- GPIO now follows the `DETECTED` presence state: multiple distinct objects
  confirm immediately, while one object must persist for three frames.
- The Pi detection gates retain single-point sensitivity for fast, sparse
  targets while using three-frame temporal confirmation to suppress flashes.

### Fixed

- Restored the mandatory range `cfarFovCfg` in `aop_presence_10fps.cfg`, which
  SDK 3.6 requires as part of a full configuration before the first
  `sensorStart`.

## [0.2.0] - 2026-07-26

### Added

- **Airborne sUAS support.** New `airborne_5m.cfg` chirp profile and `airborne`
  detection preset for 5 m single and multi-object detection from a moving
  platform. Raises max unambiguous velocity from 0.97 m/s to 13.21 m/s and the
  frame rate from 10 Hz to 20 Hz.
- **Ego-motion compensation** (`airborne.py`). Recovers platform forward speed
  by fitting `doppler = -v * cos(bearing)` across the static field, or takes it
  from telemetry via `--ego-speed`. The fit is refused rather than guessed when
  the cloud cannot constrain it.
- **Ground-plane rejection** from `--agl` and `--pitch`.
- **`--movers-only`**, trading static targets for clutter rejection.
- **Multi-object GPIO signal line.** Asserts a Raspberry Pi 5 pin while more
  than one resolvably distinct object is confirmed. Pin 1 to VCC, pin 9 to GND,
  pin 11 (BCM GPIO17) to signal. Fail-safe low on every exit path.
- **Temperature TLV parsing** (type 9) with warnings as the die approaches its
  rated limit. Direct sunlight is a thermal risk, not an RF one.
- **`outdoor` preset**, tuned on hardware during live testing.
- **`--preset`**, selecting detection gates and chirp profile together.
- **Zero-required-configuration startup.** The radar profile ships as package
  data, and `--configure auto` attaches to an already-streaming sensor.
- Headless operation under Windows PowerShell and SSH, with responsive Ctrl+C.
- `--json` output, one record per frame, with logs moved to stderr.
- `configs/detection_gates_debug.json`, naming the maximum-sensitivity gates
  honestly instead of shipping them as a default.

### Changed

- Headless is now the default interface. `--gui` opts into Qt.
- Console script renamed to `capedar`; `aop-presence` remains as an alias.
- `configs/detection_gates.json` and `detection_gates_pi.json` now carry the
  live-tuned values. They previously set `cluster_min_points` and
  `frames_to_confirm` to 1, which disabled the two stages the design relies on
  to suppress phantom detections.
- CFAR thresholds raised 15 to 18 in the airborne profile for the outdoor
  noise floor.
- CI now enforces a 70 percent coverage floor.

### Fixed

- Import ordering across the package after the `types.py` to `custom_types.py`
  rename, which had CI red on `convert-to-headless`.

### Known limitations

- `airborne_5m.cfg` is derived from the SDK chirp equations and asserted in CI,
  but has **not been validated on hardware**. Confirm it in the TI mmWave Demo
  Visualizer before flight.
- Ego-motion compensation models forward flight only. Sideslip, climb, and yaw
  rate are not modelled.
- Ground rejection assumes flat terrain at a known height.
- Two objects inside one azimuth beam return one cluster. At 5 m that is
  anything closer than 1.32 m. This is the antenna, not the software.

## [0.1.0]

### Added

- Initial release: presence detection, ranging, and bounded size estimation for
  the AWR6843AOPEVM, with a four-stage anti-phantom pipeline, a PyQt6 GUI, a
  hardware-free simulator, and a byte-exact OOB demo parser.
