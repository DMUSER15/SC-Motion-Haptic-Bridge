"""scm_motion_hw.py - the optional "motion hardware" choice of the sc-motion-bridge installers.

The pipeline ends at SimHub (External Sim "Star Citizen" on UDP 30777, plus the SC Haptics plugin
properties), so any motion platform SimHub supports works. Setting the platform up in SimHub is
never required by the installers; this choice only decides which extra help they show:

  none    no setup help from this kit (no motion rig, or you set it up in SimHub yourself)
  thanos  example: THANOS AMC controller; on Linux the installer checks its COM port with the
          separate tool simhub-thanos-linux (which also sets it up)
  other   any other SimHub-supported motion hardware; generic notes only

The choice is saved as "motion_hardware" in the installer's config.json (Linux
~/.config/sc-motion/config.json, Windows %APPDATA%\\sc-motion\\config.json). Without a saved
choice and without a terminal, the default is "none". Never a dependency: every choice, and no
choice at all, lets the installer reach DONE.
"""

KEY = "motion_hardware"
CHOICES = ("none", "thanos", "other")
DEFAULT = "none"
TITLES = {
    "none": "none / skip - no rig, or I set up my motion hardware in SimHub myself",
    "thanos": "THANOS AMC controller (example; on Linux the kit checks its COM port with simhub-thanos-linux)",
    "other": "other SimHub-supported motion hardware (generic notes only)",
}
SHORT = {"none": "none", "thanos": "THANOS AMC (example)", "other": "other SimHub-supported hardware"}


def valid(value):
    return value in CHOICES


def saved(cfg):
    """The saved choice, or None when there is none (or it is not a known value)."""
    v = (cfg or {}).get(KEY)
    return v if valid(v) else None


def choose(option, cfg, menu=False, force_menu=False):
    """(choice, how). An explicit option wins, then the saved choice; without either, a menu in a
    terminal (when menu=True), else the default. force_menu shows the menu even when saved."""
    if option:
        return option, "--motion-hw"
    s = saved(cfg)
    if s and not force_menu:
        return s, "saved"
    if menu:
        import scm_ui  # setup/linux (also on the Windows path list)
        if scm_ui.interactive():
            cur = CHOICES.index(s) if s else CHOICES.index(DEFAULT)
            i = scm_ui.choose_one("Motion hardware (optional; any platform SimHub supports works):",
                                  [TITLES[c] for c in CHOICES], cur)
            if i is not None:
                return CHOICES[i], "chosen in the menu"
    if s:
        return s, "saved"
    return DEFAULT, "default"
