"""scm_ui.py - small terminal menus for the Linux setup tools.

Every function returns the default (or None) when there is no terminal, so the tools keep
working from scripts with their command-line options.
  interactive()                       True when both stdin and stdout are a terminal
  choose_one(title, rows, default)    numbered list, Enter = default, q = cancel -> index or None
  choose_many(title, rows, defaults)  numbers like "1,3", "a" = all, Enter = defaults -> [index]
  ask_int(prompt, default, lo, hi)    Enter = default
  confirm(question, default=False)    y / n, Enter = default
"""
import sys


def interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _input(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def choose_one(title, rows, default=0):
    if not rows:
        return None
    if not interactive():
        return default
    print(title)
    for i, r in enumerate(rows):
        print("  %s %d) %s" % ("*" if i == default else " ", i + 1, r))
    while True:
        a = _input("Choose 1-%d (Enter = %d, q = cancel): " % (len(rows), default + 1)).lower()
        if a == "":
            return default
        if a in ("q", "quit"):
            return None
        if a.isdigit() and 1 <= int(a) <= len(rows):
            return int(a) - 1
        print("  please type a number from the list")


def choose_many(title, rows, defaults=()):
    if not rows:
        return []
    if not interactive():
        return list(defaults)
    print(title)
    for i, r in enumerate(rows):
        print("  %s %d) %s" % ("*" if i in defaults else " ", i + 1, r))
    dflt = ",".join(str(i + 1) for i in defaults) or "none"
    while True:
        a = _input("Choose numbers like 1 or 1,2 (a = all, Enter = %s, q = cancel): " % dflt).lower()
        if a == "":
            return list(defaults)
        if a in ("q", "quit"):
            return None
        if a in ("a", "all"):
            return list(range(len(rows)))
        try:
            picks = sorted({int(x) - 1 for x in a.replace(" ", "").split(",") if x})
        except ValueError:
            picks = []
        if picks and all(0 <= p < len(rows) for p in picks):
            return picks
        print("  please type numbers from the list")


def ask_int(prompt, default, lo, hi):
    if not interactive():
        return default
    while True:
        a = _input("%s (%d-%d, Enter = %d): " % (prompt, lo, hi, default))
        if a == "":
            return default
        if a.isdigit() and lo <= int(a) <= hi:
            return int(a)
        if a.lower() in ("q", "quit"):
            return None
        print("  please type a number from %d to %d" % (lo, hi))


def confirm(question, default=False):
    if not interactive():
        return default
    a = _input("%s [%s] " % (question, "Y/n" if default else "y/N")).lower()
    if a == "":
        return default
    return a in ("y", "yes")
