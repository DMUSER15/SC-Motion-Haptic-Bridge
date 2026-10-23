# dbox-wine/ — D-BOX HaptiSync Center installer workarounds for Wine

HaptiSync Center 1.4.0 rolls its whole install back under Wine, for three reasons. These three workarounds let it finish in the Star Citizen prefix (2026-10-15). None of them is needed on Windows.

| File | sha256 | What it does |
|---|---|---|
| `sc_shim.c` | `7a1c5440…5a84` | Source of a stand-in `sc.exe`. Wine's `sc.exe` does not implement `sc config` ("fixme:sc:wmain command not supported"), and the installer rolls back when `sc config DboxMotionEngine depend= …` fails. |
| `NetSecurity/NetSecurity.psd1` | `def8bf8b…601a` | Module manifest of a stand-in for Windows' `NetSecurity` PowerShell module (firewall cmdlets). |
| `wmic.bat` | `5f291602…eca7` | Stand-in `wmic` for the installer's Bonjour version check (`compare_bonjour_version.bat`). Wine's `wmic` has no `datafile` alias, so the check can never read `mDNSResponder.exe`'s version. It answers only that one query, with the Bonjour version bundled in HaptiSync 1.4.0 (**3.1.0.1**); everything else goes to the real `wmic`. Active only while `C:\wmicshim` is on the PATH (`WINEPATH` for the one install command). Keep the CRLF line endings. |
| `NetSecurity/NetSecurity.psm1` | `253cba65…8e34` | The six firewall cmdlets the installer calls. They only log to `C:\fwshim.log` and report success, because Wine has no Windows Firewall. |

## How the `sc.exe` shim behaves

- `sc config | failure | description | sdset | sidtype | triggerinfo | privs`: prints a success line and exits 0.
- Every other command is passed to the real `C:\windows\system32\sc.exe` with the same arguments and handles, and returns its exit code.
- One exception: `sc start DboxHaptiSyncApi` runs for real but always reports success. That web-UI service is not needed for game motion.
- Every call and its exit code is appended to `C:\scshim.log` in the prefix.
- It takes effect only when it sits **in the same folder as the installer exe**.
- It uses no C runtime and imports only `kernel32.dll`.

`sc.exe` (the build output) is not committed. `.gitignore` excludes `*.exe`. Rebuild it from `sc_shim.c`.

## Where things go (confirmed on the working machine, 2026-10-15)

- **NetSecurity module:** `<prefix>/drive_c/Program Files/PowerShell/7/Modules/NetSecurity/` holds both files. It was copied there with:
  ```fish
  set d "$pfx/drive_c/Program Files/PowerShell/7/Modules/NetSecurity"
  mkdir -p $d
  cp NetSecurity.psm1 NetSecurity.psd1 $d/
  ```
- **PowerShell 7:** installed in the prefix at `drive_c/Program Files/PowerShell/7/pwsh.exe`.
- **Evidence the shims ran:** `drive_c/scshim.log` and `drive_c/fwshim.log` exist in the prefix.

## The install that worked (2026-10-15 15:48, from fish history)

```fish
mkdir -p ~/dbox-install
cp ~/Downloads/HSC_1.4.0_setup.exe ~/Downloads/sc.exe ~/dbox-install/
cd ~/dbox-install
rm -f "$pfx/drive_c/scshim.log"
env WINEPREFIX=$pfx WINEPATH='C:\wmicshim' $wbin ./HSC_1.4.0_setup.exe 2>&1 | grep -v -E "fixme|find_reg_tz" | tee ~/dbox-sc-install.log
```

`$wbin` was the Wine binary used for the game prefix. `WINEPATH='C:\wmicshim'` puts the `wmic.bat` stand-in (third workaround, in `C:\wmicshim` in the prefix) ahead of Wine's own `wmic` for that one command.

What `C:\scshim.log` shows the installer did:

| Call | Shim action |
|---|---|
| `sc create DboxMotionEngine …` | passed through, exit 0 |
| `sc config DboxMotionEngine depend= Netman/AudioSrv/msiserver/Dhcp/SENS/netprofm/WlanSvc` | no-op |
| `sc failure …`, `sc description …` | no-op |
| `sc start DboxMotionEngine` | passed through, exit 0 |
| `sc create DboxHaptiSyncApi …` | passed through, exit 0 |
| `sc failure …`, `sc description …` | no-op |
| `sc start DboxHaptiSyncApi` | **exit 1053**, reported as success |
| `sc query DboxMotionEngine` | passed through, exit 0 |

`C:\fwshim.log` shows the only firewall call: `Set-NetFirewallRule -DisplayName "Bonjour Service" -Enabled True`.

## Limits

- **Other HaptiSync versions:** `wmic.bat` reports Bonjour **3.1.0.1**, the version bundled with HaptiSync 1.4.0. A newer installer may bundle another Bonjour version and need that number changed. `sc_motion_guide.py dbox` warns when the installer is not 1.4.0.
- **`sc.exe` build:** the build command used by the guide was checked against the original build on 2026-10-18: same kernel32-only imports.
