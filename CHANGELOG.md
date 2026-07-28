# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-07-27

### Fixed

- **Detection regression introduced in 0.2.0.** `configs/detection_gates.json`
  and `detection_gates_pi.json` had `cluster_min_points` raised from 1 to 2 and
  `cluster_eps_m` tightened from 0.40 to 0.25 m. The AWR6843AOP at 10 fps
  returns one to three points off a person, so requiring two points per cluster
  rejected most real targets. Reverted to the values that detect in the field.
  A test now pins them.
- **`clutterRemoval` set to 0 in `aop_presence_10fps.cfg`.** At 1 it subtracts
  the zero-Doppler bin, deleting anything not moving relative to the sensor.
  This is the cause of "nothing detects unless I wave the sensor around".
- **Doppler FOV gate removed from `aop_presence_10fps.cfg`,** and the range FOV
  restored. Gating Doppler at the sensor can silently delete a standing person.
- **CI was red on main.** The 4.0 m `max_range_m` default merged from
  `live_testing` put ten tests' targets outside the range gate. Those tests now
  declare the range they need instead of depending on a mutable default. The
  4.0 m default is kept.

### Added

- **`--diagnose`.** Reports how many points each gate removes, the spread of
  raw returns, and a hint naming the responsible stage. Detects the silent
  failure where points clear every gate and then form no cluster, which is
  indistinguishable from an empty room without it.
- **`sparse` preset**, for antennas returning one to three points per target.
  Single-point clusters, with anti-phantom work carried by SNR and hysteresis.
- **Live tuning flags**, so gates can be swept in the field without editing
  JSON: `--min-snr`, `--cluster-eps`, `--cluster-min-points`, `--max-elevation`,
  `--max-range`, `--min-range`.
- `configs/detection_gates_dense.json`, preserving the live-tuned values under a
  name that states what they require.
- README troubleshooting section keyed to observed field symptoms.

### Changed

- `filters.passes_field_of_view` split into `passes_angles` and `passes_height`
  so diagnostics and the hot path share one source of truth. Behaviour is
  unchanged.

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
