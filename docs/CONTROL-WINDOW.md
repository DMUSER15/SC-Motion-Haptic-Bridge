# Control window (`gui/`)

> **Private for now.** `gui/` is not exported (`tools/scm_export.py` has no `gui/` prefix). It
> is built to become the public launcher in the next major public release; until then only the
> developer profile uses it. This page is written so it can go public with the folder.

A window that does what the terminal launcher (`scripts/sc_start.py`) does, with buttons instead
of prompts, and shows the state of every part. The terminal launcher stays as it is; both use the
same code (`gui/scm_control.py` imports `sc_start.py` and calls its functions step by step).

```
python3 ~/SC-Motion-Haptic-Bridge/gui/sc_control.py           # the window
python3 ~/SC-Motion-Haptic-Bridge/gui/sc_control.py --check   # no window: print the status of every part
```

Needs PySide6 (`sudo pacman -S --needed pyside6`). Settings (menu **Settings**):
`~/.config/sc-motion/control.json`: check for updates when it opens, start the bridge when it
opens, start SimHub after the bridge self-test. Nothing is spoken: every message is in the window.

## Cards

| Card | Shows | Buttons |
|---|---|---|
| Updates | each installed app: current version, update available, problems | Check now; Show updates |
| Motion bridge | pipeline state, **heartbeat** (the light flashes with each status line of the bridge: green telemetry flowing, blue running without game data, red not responding), **certificates** (installed correctly / WARN / FAIL, TLS self-test), the start-up steps | Start / Restart motion; Stop motion; Repair; Continue anyway (after a failed certificate check) |
| SimHub | running, more than one, or **orphan** (listed, but its Wine server is gone: no window, does nothing) | Start SimHub; End orphan and restart (after a rig check) |
| RSI Launcher / Star Citizen | launcher / game running; what is still missing before a launch | Start RSI Launcher; Restart RSI Launcher… (when one is already open, perhaps without a window) |
| Wine clean-up | the last clean-up; the option to clean up when the window closes | Clean up Wine… (lists what is left in the game prefix — RSI Launcher, Gamescope, Wine helpers — and in SimHub's prefix; untick what stays; SimHub only after the rig check) |
| MFD add-on | only when installed: running / stopped; which surfaces start (tick boxes) | Restart MFD (surfaces restart in place, or the MFD starts again if it stopped; motion, SimHub and the bridge are not touched), MFD configurator |
| Logs | the newest log; a mark when a new error log was saved | Open latest log (your text editor); Log viewer |

## Order and safety (same as the terminal launcher)

1. Update check (asks first; a change to platform movement values needs YES typed, and the
   launcher is then not started by itself: the warning is shown again before you start it).
2. Blockers (recorders, replay tools, old bridges) stop the start; the certificate check (a FAIL
   waits for Repair or Continue anyway, nothing is started before that); leftovers stopped.
3. Capture server, TLS self-test, bridge.
4. Bridge self-test with zero motion, **only while SimHub is not running** (nothing reaches the rig).
5. Add-ons; then SimHub (never a second one; an orphan is ended only after you confirm).
6. The RSI Launcher starts by itself after a good start-up (setting "Start the RSI Launcher after
   the start-up", on by default; once per window). Opening the launcher is always safe for the
   rig: the game sends no motion before you are spawned, so there is no rig check here. The
   Start button works any time the game is not running; missing bridge or SimHub are shown as
   notes. A launcher left open (also one without a window) is restarted: sc-launch.sh closes
   the old one first.
7. After the game the launcher stays open; close it yourself.
8. Closing the window stops the bridge, the capture server and the add-ons; SimHub stays.
9. Nothing closes Wine by itself. Wine clean-up happens on the button, or when the window closes if
   that option is on (off by default; settings menu or the card). The game prefix is never cleaned
   while Star Citizen runs; SimHub is closed only after you confirm: the rig checklist (motion
   power OFF, nobody in the rig) or, with "Rig checklist before SimHub is closed" switched off
   (a THANOS rig parks by itself after its standby time), a Yes/No question. Declined = SimHub
   stays. The same choice applies to "End orphan and restart". Order: SimHub asked to close (wine taskkill), the prefix's
   `wineserver -k`, then whatever is left is ended. Shells with that WINEPREFIX are kept. Same code
   as `python3 ~/sc_start.py cleanup [game|simhub|all]`.

Problems go to the error log (`scm_errlog`, program `sc-control`): a child's traceback, a child
that stops, a failed step, a crash of the window.

**Repair** stops the bridge, runs `sc_lmcert.py repair` (your saved connector / certificate copies
back, backups of the current ones first), checks again, and starts the bridge again if it was
running. The game must be closed.

**Updates**: the motion bridge from its public releases (release folder or public git clone,
the same files, checks and backups as the launcher) or, for any other git checkout, from its own
remote; the THANOS connection tool (Linux, if installed) from its remote; the MFD add-on (if
installed) from a clone of its repository or its newest release. The prompt shows current →
new version and the new CHANGELOG sections. An update is refused when it would mix work (local
changes or commits not on the remote), while the game runs, or with SimHub running when the
game definition or the plugin changes.

## Adding, removing, moving cards

The window is built from a list, so parts can move between the private and the public build
without code changes elsewhere:

- `gui/panels.json`: the public cards, top to bottom (`id`, `module`, `tier: public`).
- A profile (developer mode: `tools/dev/gui_profile.py`) adds its own cards with `PANELS`
  (`tier: dev`, placed after the card named in `after`), may change the launcher (developer tee)
  and may add log sources. Without the profile file the window is the public one.
- A card is one file: `PANEL = {"id", "title"}` and `class Panel(ui_base.Panel)` with `build()`
  and `refresh(snap)` (optional `visible_for(snap)`, `on_close()`); see `gui/ui_base.py`.
- A missing card file is skipped; a card that raises is reported once in the activity log and
  the rest of the window keeps working.

To make a developer card public: move its file into `gui/panels/`, move its entry from the
profile's `PANELS` to `gui/panels.json` with `tier: public`, and make sure it uses nothing from
`tools/dev/`. To publish the window: add `gui/` to the export prefixes in `tools/scm_export.py`
(the export still refuses anything in `tools/dev/`), add this page to the exported docs, and add
a public menu entry. Windows support needs the Windows launcher's functions
(`setup/windows/sc_start_win.py`) behind the same engine; not done yet.
