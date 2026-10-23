# Updates

The launcher checks for a newer release every time you start it ("Start StarCitizen - Motion Enabled" on Linux and Windows). It looks at the newest release on GitHub, pre-releases included, before anything else starts.

| Situation | What you see |
|---|---|
| You have the newest release | one line: `update check: up to date (0.1.0-alpha)`; the launcher carries on |
| No network, or GitHub does not answer within 5 s | one line; the launcher carries on with what you have |
| A newer release exists | **the launcher pauses**: version, release notes, and the exact list of files that would change; then `[u]pdate now, [s]kip this time, or [q]uit` (Enter = skip) |
| The update changes anything that shapes platform movement | a warning that **platform movement values HAVE changed**; you must type `YES` before it updates; the warning is shown again at the rig pause |
| Star Citizen is already running (the launcher was started again mid-game) | one line; update after the game |

Skipping is always safe: the launcher starts the version you have, and asks again next time. To turn the check off: set the environment variable `SC_UPDATE_CHECK=0`.

## What an update changes, and what it never touches

An update replaces **only the files whose content differs** from the release (compared by sha256), and adds files that are new in it. It never deletes a file. Every file it downloads must match the sha256 in the release's `release.json` before anything is replaced; if one does not match, nothing is changed.

Before a file is replaced, the old copy goes to `~/sc_motion_update_backup/<old>_to_<new>_<time>/` (Windows: `%USERPROFILE%\sc_motion_update_backup\…`), with a `manifest.json` listing what changed.

**Never touched by an update:**

- your SC Haptics effects (`effects.json`): `config/effects.json` in the release folder, `~/sc_haptics/effects.json`, `%USERPROFILE%\sc_haptics\effects.json`;
- SimHub's own settings and profiles: the Motion tab (axes, intensity, smoothing, limits, crash protection), Motion haptics gains, ShakeIt effects, controllers and COM ports;
- your certificate pair (`lm.crt`, `lm.key`) and D-BOX's files in the game prefix;
- the locations the installer saved (`config.json`).

**Changed only when the release changed them, and with the launcher's warning:**

| Release changes … | The update also … |
|---|---|
| the bridge, the capture server, its schema or the effects engine (`scripts/lm_simhub*.py`, `SC_Motion_Server.py`, `motion_desc.bin`, `sc_fx.py`) | nothing else; these are used at the next start |
| SimHub's game definition (`config/simhub/Star Citizen.simdef`) | installs it into SimHub and registers it; your previous one is renamed to `Star Citizen.simdef.bak-<time>` (SimHub must be closed) |
| the SC Haptics plugin source (`plugin/src/`) | rebuilds the plugin with `dotnet` and installs it; the previous DLL goes to `~/sc_haptics/backup` (SimHub must be closed) |
| the default effects (`plugin/effects.default.json`) | nothing; your `effects.json` stays as it is. Compare the two files if you want the new defaults |

When the update would replace the game definition or the plugin while SimHub is running, the launcher asks you to close SimHub first.

## After an update that changed platform movement values

The warning means a file that shapes what reaches the motion platform has changed, so the platform can move differently with the same SimHub settings. Before anyone rides:

1. Check your SimHub Motion settings for Star Citizen: the Motion tab (axes, intensity, smoothing, limits) and Motion haptics (gain, smoothing). Adjust them for your platform.
2. First run with the E-stop within reach:
   1. Nobody in the rig, motion power OFF, SimHub motion OFF; check the numbers in SimHub.
   2. Motion power ON, still nobody in the rig.
   3. Only then a person.

The bridge sends the game's values unshaped; all shaping and limits are yours, in SimHub.

## Kinds of install

| You installed by … | The launcher's update … |
|---|---|
| downloading and unpacking a release | replaces the changed files in that folder |
| `git clone` of this repository | runs `git fetch` and fast-forwards to the release tag. If you changed tracked files other than `config/effects.json`, git refuses and nothing changes; update by hand with git |
| Windows | updates the repository folder the installer saved, then copies the runtime files into `%USERPROFILE%\sc-motion` again (the same files as installer step 6) |

Keep the folder you installed from: it is the one that gets updated. If you moved or deleted it on Windows, the launcher can only tell you about the new release; download it and run `Install-SCMotion.ps1 -Apply` from the new folder (your settings are kept).

After an update that touched setup files, you can run the verifier as usual (`sc_motion_verify.py` / `sc_verify_win.py`).

## Undo an update

Copy the files from the backup folder back over the release folder (same relative paths), including `release.json`. If the game definition was replaced, rename `Star Citizen.simdef.bak-<time>` back in SimHub's `PluginsData/Common/ExternalSims` folder (SimHub closed). The next start offers the update again; answer `s`.
