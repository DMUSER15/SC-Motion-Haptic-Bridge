#!/usr/bin/env python3
"""fields_db.py - check and print the telemetry field database (telemetry/sc_fields.json).

The database is the source of truth for what the game's telemetry posts mean: events, fields,
types, units, how sure we are (confidence) and value names (enum). Add-ons read it at every start
(add-on contract v1), so it must always load. Adding events, fields or keys is never a contract
change; removing or renaming a key, or changing a type or unit, is.

  python3 tools/fields_db.py check [FILE]            exit 0 = usable and clean, 3 = problems
  python3 tools/fields_db.py list [FILE]             one line per field
  python3 tools/fields_db.py table [FILE]            the field table as Markdown
  python3 tools/fields_db.py observe RECORDING...    compare recordings (.ndjson) with the database:
                                                     fields seen but not listed, and values whose
                                                     type differs from the listed one (read-only)
"""
import collections
import json
import math
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(REPO, "telemetry", "sc_fields.json")
SCHEMA = "sc-telemetry-fields"
CONTRACT = 1
TYPES = ("int", "float", "xyz", "string")
CONFIDENCE = ("Observed", "Probable", "Inferred", "Unknown")
SUITABILITY = ("high", "medium", "low", "none", "")
POST_KEYS = ["t", "ev", "name", "m", "v"]
MAX_BYTES = 2_000_000


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load(path=None):
    path = path or os.environ.get("SC_FIELDS_DB") or DEFAULT
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("file larger than %d bytes" % MAX_BYTES)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def problems(db):
    """Every reason this database breaks the contract or its own rules (empty = clean)."""
    out = []
    if not isinstance(db, dict):
        return ["not a JSON object"]
    if db.get("schema") != SCHEMA:
        out.append("schema must be %r" % SCHEMA)
    if db.get("contract") != CONTRACT:
        out.append("contract must be %d" % CONTRACT)
    if not isinstance(db.get("db_version"), str) or not re.match(r"^\d{4}-\d\d-\d\d\.\d+$", db.get("db_version", "")):
        out.append("db_version must look like 2026-11-16.1")
    post = db.get("post")
    if not isinstance(post, dict) or post.get("format") != "json" or post.get("keys") != POST_KEYS:
        out.append("post must be {format: json, keys: %s, ...}" % POST_KEYS)
    elif not isinstance(post.get("synthetic_names"), list) or not all(isinstance(x, str) for x in post["synthetic_names"]):
        out.append("post.synthetic_names must be a list of names")
    taps = db.get("taps")
    if not isinstance(taps, dict) or not isinstance(taps.get("supported"), bool):
        out.append("taps.supported must be true or false")
    elif taps["supported"]:
        r = taps.get("ports")
        if not (isinstance(r, list) and r and all(isinstance(x, list) and len(x) == 2 and all(_int(y) for y in x)
                                                  and 1024 <= x[0] <= x[1] <= 65535 for x in r)):
            out.append("taps.ports must be a list of [low, high] port ranges when taps are supported")
        if "run" in taps and not isinstance(taps["run"], bool):
            out.append("taps.run must be true or false")
    events, fields = db.get("events"), db.get("fields")
    if not isinstance(events, list) or not isinstance(fields, list):
        return out + ["events and fields must be lists"]
    evs, names = set(), set()
    for i, e in enumerate(events):
        if not isinstance(e, dict) or not _int(e.get("ev")):
            out.append("event #%d: needs an integer 'ev'" % i)
            continue
        tag = "event %d" % e["ev"]
        if e["ev"] in evs:
            out.append(tag + ": listed twice")
        evs.add(e["ev"])
        for k in ("name", "label"):
            if not isinstance(e.get(k), str) or not e[k]:
                out.append("%s: needs a text '%s'" % (tag, k))
        if isinstance(e.get("name"), str):
            if e["name"] in names:
                out.append("%s: name %r used twice" % (tag, e["name"]))
            names.add(e["name"])
        if e.get("confidence") not in CONFIDENCE:
            out.append("%s: confidence must be one of %s" % (tag, ", ".join(CONFIDENCE)))
    for r in db.get("resets") or []:
        if not isinstance(r, dict) or not _int(r.get("on_ev")) or not isinstance(r.get("clear_ev"), list) \
                or not all(_int(x) for x in r["clear_ev"]):
            out.append("resets: each entry needs integer on_ev and a list clear_ev")
        elif r["on_ev"] not in evs or any(x not in evs for x in r["clear_ev"]):
            out.append("resets: on_ev %s / clear_ev %s name an event that is not listed" % (r["on_ev"], r["clear_ev"]))
    keys = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict) or not _int(f.get("ev")) or not _int(f.get("id")):
            out.append("field #%d: needs integer 'ev' and 'id'" % i)
            continue
        key = "ev%d.id%d" % (f["ev"], f["id"])
        if f.get("key") != key:
            out.append("field %s: 'key' must be %s (is %r)" % (key, key, f.get("key")))
        if key in keys:
            out.append("field %s: listed twice" % key)
        keys.add(key)
        if f["ev"] not in evs:
            out.append("field %s: event %d is not in the events list" % (key, f["ev"]))
        if f.get("type") not in TYPES:
            out.append("field %s: type must be one of %s" % (key, ", ".join(TYPES)))
        for k in ("name", "label"):
            if not isinstance(f.get(k), str) or not f[k]:
                out.append("field %s: needs a text '%s'" % (key, k))
        for k in ("short", "unit", "notes"):
            if k in f and not isinstance(f[k], str):
                out.append("field %s: '%s' must be text" % (key, k))
        if f.get("confidence") not in CONFIDENCE:
            out.append("field %s: confidence must be one of %s" % (key, ", ".join(CONFIDENCE)))
        if f.get("mfd", "") not in SUITABILITY:
            out.append("field %s: mfd must be one of high, medium, low, none" % key)
        if "range" in f:
            r = f["range"]
            if not (isinstance(r, list) and len(r) == 2 and all(_num(x) for x in r) and r[0] <= r[1]):
                out.append("field %s: range must be [low, high] numbers" % key)
        if "enum" in f:
            en = f["enum"]
            if not isinstance(en, list) or not all(isinstance(x, dict) and _num(x.get("value")) and isinstance(x.get("name"), str)
                                                   and x.get("name") for x in en):
                out.append("field %s: enum entries need a number 'value' and a text 'name'" % key)
            elif len({x["value"] for x in en}) != len(en):
                out.append("field %s: an enum value is listed twice" % key)
    return out


def value_type(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return "xyz"
    return "list[%d]" % len(v) if isinstance(v, list) else type(v).__name__


def type_fits(listed, seen):
    return seen == listed or (listed == "float" and seen == "int")     # JSON writes 1.0 as 1 in places


def observe(db, paths):
    """-> (seen {key: Counter of types}, posts read, bad lines)."""
    seen = collections.defaultdict(collections.Counter)
    posts = bad = 0
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if not isinstance(p, dict) or not _int(p.get("ev")):
                    continue                                   # marks and other notes
                if p.get("name") in ((db.get("post") or {}).get("synthetic_names") or []):
                    continue
                m, v = p.get("m"), p.get("v")
                if not isinstance(m, list) or not isinstance(v, list) or len(m) != len(v):
                    bad += 1
                    continue
                posts += 1
                for k, val in zip(m, v):
                    if _int(k):
                        seen["ev%d.id%d" % (p["ev"], k)][value_type(val)] += 1
    return seen, posts, bad


def cmd_check(path):
    try:
        db = load(path)
    except (OSError, ValueError) as e:
        print("FAIL: cannot read the field database: %s" % e)
        return 3
    probs = problems(db)
    for p in probs:
        print("PROBLEM: " + p)
    conf = collections.Counter(f.get("confidence") for f in db.get("fields", []) if isinstance(f, dict))
    print("RESULT: %s - db_version %s, %d events, %d fields (%s)" % (
        "FAIL, %d problems" % len(probs) if probs else "PASS", db.get("db_version"), len(db.get("events", [])),
        len(db.get("fields", [])), ", ".join("%s %d" % (c, conf[c]) for c in CONFIDENCE if conf[c])))
    return 3 if probs else 0


def cmd_list(path):
    db = load(path)
    for f in sorted(db["fields"], key=lambda x: (x["ev"], x["id"])):
        print("%-11s %-6s %-9s %-24s %s%s" % (f["key"], f["type"], f.get("confidence", ""), f["name"], f["label"],
                                             (" [%s]" % f["unit"]) if f.get("unit") else ""))
    return 0


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def cmd_table(path):
    db = load(path)
    ev = {e["ev"]: e for e in db["events"]}
    print("# Telemetry field table (generated from telemetry/sc_fields.json %s)\n" % db["db_version"])
    print("Do not edit this table: change the database and run `python3 tools/fields_db.py table`.\n")
    for n in sorted(ev):
        e = ev[n]
        print("## ev%d %s (%s; %s)\n" % (n, e["label"], e.get("rate", "?"), e.get("confidence", "?")))
        if e.get("notes"):
            print(_cell(e["notes"]) + "\n")
        rows = [f for f in db["fields"] if f["ev"] == n]
        if not rows:
            print("No fields listed.\n")
            continue
        print("| Field | Name | Meaning | Type | Unit | Confidence | Values / notes |")
        print("|---|---|---|---|---|---|---|")
        for f in sorted(rows, key=lambda x: x["id"]):
            extra = "; ".join("%s = %s" % (x["value"], x["name"]) for x in f.get("enum") or [])
            if f.get("notes"):
                extra = (extra + "; " if extra else "") + f["notes"]
            print("| %s | %s | %s | %s | %s | %s | %s |" % (f["key"], _cell(f["name"]), _cell(f["label"]), f["type"],
                                                            _cell(f.get("unit", "")), f.get("confidence", ""), _cell(extra)))
        print()
    return 0


def cmd_observe(paths):
    db = load(None)
    listed = {f["key"]: f for f in db["fields"]}
    seen, posts, bad = observe(db, paths)
    new = sorted(k for k in seen if k not in listed)
    differ = sorted((k, dict(c)) for k, c in seen.items() if k in listed and any(not type_fits(listed[k]["type"], t) for t in c))
    never = sorted(k for k in listed if k not in seen)
    print("%d posts read from %d file(s), %d unusable lines" % (posts, len(paths), bad))
    for k in new:
        print("NEW     %-11s seen as %s  (not in the database)" % (k, dict(seen[k])))
    for k, c in differ:
        print("TYPE    %-11s listed %s, seen %s" % (k, listed[k]["type"], c))
    print("RESULT: %d new, %d type differences, %d listed fields not in these recordings" % (len(new), len(differ), len(never)))
    return 0


def main(a):
    cmd = a[1] if len(a) > 1 else ""
    try:
        if cmd == "check" and len(a) <= 3:
            return cmd_check(a[2] if len(a) == 3 else None)
        if cmd == "list" and len(a) <= 3:
            return cmd_list(a[2] if len(a) == 3 else None)
        if cmd == "table" and len(a) <= 3:
            return cmd_table(a[2] if len(a) == 3 else None)
        if cmd == "observe" and len(a) >= 3:
            return cmd_observe(a[2:])
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("FAIL: %s: %s (run: python3 tools/fields_db.py check)" % (type(e).__name__, e))
        return 3
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
