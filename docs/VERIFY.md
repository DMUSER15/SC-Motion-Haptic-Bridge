# Verify the installation

Linux:   `python3 ~/SC-Motion-Haptic-Bridge/setup/linux/sc_motion_verify.py [--live] [--last-session]`
Windows: `py -3 %USERPROFILE%\sc-motion\sc_verify_win.py [--live] [--last-session]`

Every line is **PASS**, **WARN**, **FAIL**, **INFO** or **SKIP**. A WARN or FAIL comes with a `fix:` line. The report is saved to `~/lm_capture/verify/verify_<time>.txt` (Windows: `%USERPROFILE%\lm_capture\verify\`). Attach that file when asking for help.

Exit code: 0 PASS, 9 WARN, 10 FAIL, 2 when `--live` refused because something was running.

## The three levels

| Level | Needs | Proves |
|---|---|---|
| plain | nothing; read-only | every file, path, certificate, port and setting is in place |
| `--live` | game, SimHub and pipeline **closed** | the capture server starts and serves the certificate the game trusts; the bridge turns frames into SimHub packets; the tee feeds the haptics plugin |
| `--last-session` | one real game session started from the menu entry or shortcut | the game connected, registered its events and streamed vehicle frames; the bridge saw you seated |

Together they cover the whole chain except SimHub's own motion output to the rig. That one you see in SimHub and feel in the seat.

## What each check means (Linux names; Windows is the same where it applies)

| Area | Check | PASS means | If not |
|---|---|---|---|
| system | python, module grpc / google.protobuf, openssl | runtime for the capture server and certificate tools | install packages (INSTALL guides, §1) |
| repo | bridge/plugin files linked | the 16 files needed to run are symlinks into the repository | installer step 9, or `tools/repo_link.py link --adopt-home` |
| repo | scripts compile | no syntax errors in the tracked scripts | fix the named file |
| repo | uncommitted changes (INFO) | how many changes are not committed yet | `git -C ~/SC-Motion-Haptic-Bridge status` |
| game | LUG prefix, sc-launch.sh runner, LIVE installed | the game can be started the way `sc_start.py` does it | LUG-Helper |
| game | env for non-default paths | variables are set for manual commands | run the `set -Ux` lines shown |
| dbox | HaptiSync connector installed | `LiveMotionConnector` exists | install D-BOX HaptiSync Center (§3) |
| dbox | StarCitizen connector folder | the folder the game loads from is complete | installer step 4 |
| dbox | connector config | 127.0.0.1, 61666, LiveMotionComm | compare with the LiveMotionConnector copy |
| dbox | livemotion.crt copies (INFO) | normally: all D-BOX original except the game's copy = `lm.crt` | `sc_lmcert.py status` |
| dbox | connector log (game side) | the D-BOX connector's own log has no TLS or connection errors | see `--last-session` |
| cert | livemotion.crt.orig | backup of D-BOX's original exists | installer step 6 |
| cert | sc_lmcert.py check | the StarCitizen and LiveMotionConnector copies = `lm.crt`, key matches, ≥ 30 days left (WARN under 30) | `sc_lmcert.py repair` after a patch; `new` + `activate` to renew |
| cert | motion_desc.bin | the schema the capture server loads parses and equals the repository copy | installer step 5; `python3 tools/motion_schema.py check` |
| simhub | prefix, wine, SimHubWPF.exe | SimHub installed where `sc_start.py` looks | linux-simracing-utils (§4) |
| simhub | game definition 'Star Citizen' | a definition is registered for id `2f10c0be-…b874` and the bridge can read it (fields, signature, revision shown) | installer step 7 (Windows step 8), SimHub closed |
| simhub | active game | the game SimHub's log last started: PASS Star Citizen, WARN another game (SimHub then waits for that game's data), INFO no log yet | select Star Citizen in SimHub's game list |
| simhub | SC Haptics plugin | built DLL = installed DLL | installer step 8 |
| simhub | effects.json | valid JSON (the plugin keeps the last good one) | fix the JSON |
| motion | motion hardware | your installer choice (none / THANOS example / other); information only | `sc_motion_install.py motion-hw …` |
| motion | THANOS COM port | THANOS choice only: simhub-thanos-linux `plan` reports `READY` (PASS); not ready = WARN; tool not installed = INFO | `python3 ~/simhub-thanos-linux/simhub_thanos.py plan` / `apply` |
| motion | USB serial COM ports in SimHub's prefix | `other` or no choice: listed as INFO, never WARN or FAIL | — |
| launch | menu entry | runs an existing `sc_start.py` | installer step 9 |
| launch | remove-setup menu entry | "Star Citizen - Remove Motion Setup" exists (WARN if missing) | installer step 9 |
| runtime | pipeline ports free | 61666/tcp and 61700–61702/udp are free when the pipeline is not running | stop the program named in the line |
| live | capture server listening | `SC_Motion_Server.py` started and listens on 127.0.0.1:61666 | run it by hand to see the error |
| live | TLS self-test | a TLS client that trusts only the game's `livemotion.crt` accepts the server as LiveMotionComm | `sc_lmcert.py status` |
| live | bridge listening | `lm_simhub4t.py` bound UDP 61700 | `ss -ulpn` shows who holds it |
| live | bridge → SimHub packet | zero frames in; packets of the registered definition's size and signature (134 bytes for the 24-field one) with zero motion out | bridge log in `lm_capture/verify` |
| live | tee → plugin copy | the tee copies every datagram to UDP 61701 | bridge log |
| session | game connected (GetIdentification) | the game's connector reached the capture server and was accepted | certificate (`sc_lmcert.py status`), connector log |
| session | telemetry stream opened | the game opened the event stream | as above |
| session | vehicle frames (ev3) | number of vehicle frames received; other event counts listed | sit in a pilot seat with engines on |
| session | bridge saw you seated with motion | lines with `active=1 seat=1` in the bridge log | the seat gate needs the pilot seat |

## Expected results on a complete setup

- **plain:** `RESULT: PASS`, or `WARN` only for things that are not plugged in (the THANOS COM port) or optional.
- **`--live`:** five `live` PASS lines:
  ```
  PASS  live  capture server listening           127.0.0.1:61666
  PASS  live  TLS self-test (game's certificate) SELFTEST PASS: server on 127.0.0.1:61666 validates against ProgramData/D-BOX/Gaming/LiveMotion/StarCitizen/livemotion.crt as LiveMotionComm (...)
  PASS  live  bridge listening                   UDP 61700 (lm_simhub4t.py)
  PASS  live  bridge -> SimHub packet            bridge answered with 134-byte packets, motion values all zero
  PASS  live  tee -> plugin copy (UDP 61701)     byte-for-byte copy received
  ```
- **`--last-session`** after flying:
  ```
  PASS  session  game connected (GetIdentification) yes
  PASS  session  telemetry stream opened            yes
  PASS  session  vehicle frames (ev3)               152000 frames; events 0:..., 3:..., 10:...
  PASS  session  bridge saw you seated with motion  N status lines with active=1 seat=1
  ```

## In SimHub and on the rig (by eye)

1. SimHub shows the game **Star Citizen** connected while you sit in a pilot seat.
2. The telemetry values move with the ship: yaw, pitch and roll rates; surge, sway and heave.
3. The SC Haptics tab (Additional plugins) shows packets per second and `Seated`.
4. **Nobody in the rig and motion power (cabinet) OFF first**, then motion power ON with nobody in the rig, then you.
