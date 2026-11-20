# Architecture

How the pieces fit together. Findings are tagged Observed (seen on a real machine) or Probable/Inferred where they are not yet proven.

## Data path

```
Star Citizen (D-BOX LiveMotion output)
  -> D-BOX connector dbxLive64.dll (loaded by the game from ProgramData\D-BOX\Gaming\LiveMotion\StarCitizen\)
  -> gRPC + TLS to 127.0.0.1:61666, server name LiveMotionComm, trusts only livemotion.crt in that folder
  -> SC_Motion_Server.py: GetIdentification (client type 2 supported) + LiveMotionStream; one JSON datagram per event
       {"t": receive time, "ev": event key, "name": ..., "m": [field meanings], "v": [values]}
  -> UDP 61700 -> lm_simhub4t.py tee (copies every datagram byte for byte; add-on taps last)
       -> UDP 61702 -> lm_simhub4.py bridge (lm_simhub3 motion + haptics fields), 100 Hz
                    -> UDP 30777 -> SimHub External Sim "Star Citizen" (registered .simdef)
       -> UDP 61701 -> SC Haptics plugin -> SCHaptics.* properties -> ShakeIt "SC Low"/"SC High"
```

| Port | Proto | Owner |
|---|---|---|
| 61666 | TCP (TLS, h2) | capture server `SC_Motion_Server.py` |
| 61700 | UDP | tee input (`lm_simhub4t.py`; `lm_simhub3t.py` for the 9-field bridge) |
| 61701 | UDP | SC Haptics plugin (inside SimHub) |
| 61702 | UDP | bridge (`lm_simhub4.py` behind the tee) |
| 30777 | UDP | SimHub External Sim |

The numbers live in one place, `setup/common/scm_ports.py` (`python3 setup/common/scm_ports.py` prints the table). The launchers, installers and verifiers read it. The capture server, tee, bridge and plugin keep their own numbers, because those files are released and checked byte for byte. 61703, 61709 and 61710 are reserved. 61704-61708 and 61720-61739 are kept for add-ons.

"Is this program running" is decided by one rule, `setup/common/scm_proc.py`: the executable, or for `python [-options] script.py` the script. The launcher, the Linux setup code and the Windows helper all use it. On Linux a SimHub process whose Wine server is gone counts as stuck, not as running.

## Public surface (what other programs may rely on)

Add-ons and other tools use only what is listed here. They never import the bridge's Python modules: file names, functions and internal ports can change in any release.

| Surface | What it is | Stability |
|---|---|---|
| Field database `telemetry/sc_fields.json` | every event and field: key `ev<N>.id<M>`, name, type (`int`, `float`, `xyz`, `string`), unit, confidence, value names | `"contract": 1`. Adding events, fields or keys is not a contract change. Removing or renaming a key, or changing a type or unit, is. |
| Telemetry posts | the capture server's JSON posts `{"t", "ev", "name", "m", "v"}`, one per UDP datagram on 127.0.0.1, exactly as the tee receives them | part of contract 1. Posts whose `name` is in `post.synthetic_names` are the launcher's self-test, not game data. |
| Ports | `setup/common/scm_ports.py`: the bridge's own ports and the add-on ranges | an add-on never binds a bridge port |
| Settings folder | `~/.config/sc-motion/` (Windows `%APPDATA%\sc-motion`): `config.json` | keys may be added |
| Add-on packages | `addon.json` + `release.json` at the top of an add-on; installed by `setup/common/scm_addons.py` into `~/.local/share/sc-motion/addons/<name>/<version>/` | part of contract 1 (see docs/UPDATING.md, Optional add-ons) |
| Error logs | `~/.local/state/sc-motion/logs` (Windows `%LOCALAPPDATA%\sc-motion\logs`), written by `scm_errlog.py` | text for people, not a data format |

Check the database with `python3 tools/fields_db.py check`. `python3 tools/fields_db.py table` prints it as a table, and `observe RECORDING` compares a recording with it. Every finding about what a field means goes into the database first; documents follow from it.

### Add-on taps (contract v1 section 3)

An add-on asks for its own copy of the telemetry with one file, `~/.config/sc-motion/taps.d/<name>.json` (Windows `%APPDATA%\sc-motion\taps.d`):

```json
{"name": "mfd", "port": 61704, "enabled": true, "run": ["{python}", "-u", "/abs/path/sc_mfd.py", "run"]}
```

- **The tee** reads the folder once when it starts and sends every post, byte for byte, to each usable tap **after** the bridge and plugin copies, on its own non-blocking socket. A tap that is missing, slow or broken never delays or changes the bridge and plugin copies; its send errors are counted in the tee's stats line (`mfd_err=0`). Any problem reading the files only means no taps. `LM_TEE_TAPS=0` turns all copies off.
- **The launcher** starts each tap's `run` command after its self-test (so add-ons never see the test frames) and stops it, Ctrl-C first, when the pipeline stops; `sc_start.py stop` also ends one left from a crash. An add-on that stops by itself is reported once and never stops the motion pipeline. Its output goes to `~/lm_capture/launch/addon_<name>.log`. It gets `SC_BRIDGE_DIR` (where the bridge and its field database are). `SC_START_NO_ADDONS=1` starts none.
- **Rules** (`scripts/scm_taps.py`; `python3 ~/scm_taps.py` lists the taps and why any are skipped): the name equals the file name; the port is in 61704-61708 or 61720-61739, is not a bridge port and is not used by an earlier file; at most 8 taps. `run` must be `{python}`, Python options (not `-c` / `-m`), then the absolute path of an existing `.py` file and its arguments. No shell is involved.
- The verifier lists the taps, and `--live` checks that each one gets a byte-for-byte copy.
- The field database says `"taps": {"supported": true, "ports": [...], "run": true}`; add-ons read that to choose their own mode.

## SimHub packet

Header `<IIHHQBQBBQBBBBdI` (55 bytes, packed, GameSignature `0x450BC1A7`), then the fields of SimHub's registered game definition in its order (Float32, Byte or UInt16 per field). `lm_simhub4.py` reads the definition SimHub has registered (`ExternalSims/Registrations/2f10c0be-….simlink` in SimHub's AppData\Local) and takes the TelemetrySignature from it, so the packet always matches what SimHub expects. `python3 ~/lm_simhub4.py layout` prints the layout.

- 9 fields (`lm_simhub3.py`, and the original definition): `<IIHHQBQBBQBBBBdI9f`, 91 bytes, TelemetrySignature `0x0696EF94`.
- 24 fields (`config/simhub/Star Citizen.simdef`, data conversion revision 3): 134 bytes. The nine motion values, then SpeedKmh, EngineRpm, EngineMaxRpm, FlightIsOnGround, the four SuspensionVelocity fields, EngineStarted, EngineIgnitionOn, Throttle and the four TyreContactSurface fields, for SimHub's Motion haptics.

The nine motion values are:

- yaw, pitch and roll rate (deg/s);
- surge, sway and heave (m/s² × `LM_SCALE`, default 0.1);
- velocity forward, lateral and up (m/s).

The header's "active" byte is 0 when there has been no vehicle frame for 0.3 s.

## Bridge (lm_simhub3.py; lm_simhub4.py adds the haptics fields on top, motion unchanged)

**Mapping.** Inputs are ev3 id35 (angular velocity, rad/s), id40 (acceleration incl. gravity and drag, body axes) and id9 (velocity). Axes: x right, y forward, z up.

- yaw = −w[2]
- pitch = −w[0]
- roll = +w[1]
- surge = a[1]
- sway = a[0]
- heave = a[2]

**Filters.** These handle noise and faults only. All shaping is in SimHub, by decision.

- Landed gate: gear id92 ≥ 0.99 and |v| < 0.5 → linear motion 0.
- Glitch hold: |a| > 300 or a non-finite value → hold the last good frame.
- Stale ramp: 0.3 s without a frame → ramp to 0 over 0.5 s.
- Seat gate: motion only while ev10 id38 == 1 (no older than 0.25 s); ev2 means not seated; fade 0.5 s.

## Events (ev numbers)

| ev | Event | Notes |
|---|---|---|
| 0 | Global | id93 = frame dt |
| 1 | Enter Vehicle Config | ship name |
| 2 | Leave Vehicle | |
| 3 | Vehicle Frame | 120 B |
| 4 | Vibration | id41 = boost flag, id43 = atmospheric rumble |
| 5 | Weapon | id171: 1 ballistic, 0 energy |
| 6 | Explosive Ordnance | missile |
| 7 | Vehicle Impact | not posted yet |
| 8 | Vehicle Explosion Impact | not posted yet |
| 9 | Decoupled | |
| 10 | Player Role | 1 seated, 0 on foot, 3 settling |
| 11 | Turret | |
| 12 | Actor | |
| 13 | Recoil | |
| 14 | Heartbeat | ~1 Hz |
| 15 | Force Reaction | not posted yet |
| 16 | Footstep | not seen yet |
| 17 | Audio Haptic | not seen yet |
| 18 | Ground Vehicle | |
| 19 | Throw | not seen yet |

## Certificate model

- The connector trusts one `livemotion.crt`: until 2026-10-18 the copy in `…\LiveMotion\StarCitizen\`, on 2026-10-21 the copy in `…\LiveMotion\LiveMotionConnector\` (Observed; probably a game patch). D-BOX ships a certificate whose private key is not available.
- This project generates its own pair: RSA 4096, CN and SAN LiveMotionComm, basicConstraints critical CA:TRUE, 10 years. It puts that certificate into every copy (`footprint all`); `sc_lmcert.py check` checks both copies the connector may read, and `repair` restores them after an update. `footprint minimal` (StarCitizen copy only) no longer works with the current game.
- Without D-BOX HaptiSync installed, the game still loads the connector if `dbxLive64.dll` is in the StarCitizen folder and its config and certificate are in the LiveMotionConnector folder (test prefix, 2026-10-21, Observed).
- `sc_motion_clean.py` undoes it: D-BOX's original in every copy, connector folder and plugin moved to a backup.
- All changes go through `sc_lmcert.py` / `sc_certswap.py`. Both:
  - refuse while the game runs;
  - back up every copy with a manifest;
  - write atomically and verify the sha256 afterwards.

Alternatives that were tested:

- `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`: ignored by the connector.
- D-BOX's own API: settings only, no telemetry out.

An official open telemetry output from RSI would remove the need for any certificate change.

## Platform differences

| | Linux | Windows |
|---|---|---|
| Game | LUG-Helper Wine prefix, `sc-launch.sh` | native RSI Launcher |
| D-BOX install | HaptiSync Center in the game prefix, with two installer workarounds; its UI does not run | native installer |
| SimHub | linux-simracing-utils prefix, own Wine | native |
| Launcher | `sc_start.py` (menu entry) | `sc_start_win.py` (Start menu shortcut) |
| Certificates | `python3 ~/sc_lmcert.py …` | `py -3 sc_cert_win.py lmcert …` (same script, Windows process checks) |
| Plugin config | `~/sc_haptics/effects.json` (found through Wine's Z: drive) | `%USERPROFILE%\sc_haptics\effects.json` (plugin 0.2.3; `SC_HAPTICS_CONFIG` overrides on both) |
