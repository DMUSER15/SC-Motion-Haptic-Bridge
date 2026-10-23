#!/usr/bin/env python3
"""sc_cert_win.py - run the unchanged sc_lmcert.py and sc_certswap.py on Windows.

  py -3 sc_cert_win.py lmcert status|check|selftest|new [copy-subject]|activate|rollback|footprint minimal|all
  py -3 sc_cert_win.py certswap status|original|custom

Same commands, rules, backups and exit codes as on Linux (see those scripts). Differences:
  - the "prefix" is the Windows drive itself: SC_CERT_DRIVE_C defaults to C:\\ , so the copies
    live in C:\\ProgramData\\D-BOX and C:\\Program Files\\D-BOX;
  - process checks (StarCitizen.exe, SC_Motion_Server.py) use winproc.py instead of /proc;
  - openssl.exe is looked for on PATH, in C:\\Program Files\\OpenSSL-Win64\\bin and in Git.
Writing under C:\\ProgramData\\D-BOX may need an elevated (Run as administrator) PowerShell.
lm.crt / lm.key / lm_certs / livemotion.crt.orig live in your user folder (%USERPROFILE%),
like ~ on Linux. This file must sit next to sc_lmcert.py, sc_certswap.py and winproc.py.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SC_CERT_DRIVE_C", os.environ.get("SystemDrive", "C:") + "\\")

import winproc  # noqa: E402


def _openssl_on_path():
    import shutil
    if shutil.which("openssl"):
        return True
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for d in (os.path.join(pf, "OpenSSL-Win64", "bin"), os.path.join(pf, "Git", "usr", "bin"),
              os.path.join(pf, "Git", "mingw64", "bin")):
        if os.path.isfile(os.path.join(d, "openssl.exe")):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return True
    return False


def load():
    """Import sc_certswap with Windows process checks, then sc_lmcert (which imports it)."""
    import sc_certswap as cs
    cs.procs_with_arg = winproc.procs_with_arg
    import sc_lmcert as lm
    return cs, lm


def main(argv):
    if len(argv) < 2 or argv[1] not in ("lmcert", "certswap"):
        print(__doc__)
        return 1
    if not _openssl_on_path():
        print("openssl.exe not found. Install it (winget install -e --id ShiningLight.OpenSSL.Light) "
              "or Git for Windows, then open a new PowerShell.")
        return 3
    cs, lm = load()
    if argv[1] == "certswap":
        return cs.main(["sc_certswap.py"] + argv[2:])
    return lm.main(["sc_lmcert.py"] + argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
