# Architecture

How the pieces fit together. Findings are tagged Observed (seen on a real machine) or Probable/Inferred where they are not yet proven.

## Data path

```
Star Citizen (D-BOX LiveMotion output)
  -> D-BOX connector dbxLive64.dll (loaded by the game from ProgramData\D-BOX\Gaming\LiveMotion\StarCitizen\)
  -> gRPC + TLS to 127.0.0.1:61666, server name LiveMotionComm, trusts only livemotion.crt in that folder
  -> SC_Motion_Server.py: GetIdentification (client type 2 supported) + LiveMotionStream; one JSON datagram per event
       {"t": receive time, "ev": event key, "name": ..., "m": [field meanings], "v": [values]}
  -> UDP 61700 -> lm_simhub4t.py tee (copies every datagram byte for byte)
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
