#!/usr/bin/env python3
"""lm_simhub4t.py - lm_simhub4 bridge (v3 motion + Motion-haptics fields) + raw-event tee.

New file; a copy of lm_simhub3t.py that runs lm_simhub4 instead of lm_simhub3 (which
stays the fallback). Packet layout comes from SimHub's "Star Citizen.simdef" (see
lm_simhub4.py). Where the bridge listens:

  capture server -> UDP 61700 -> [tee in this process]
                                   |-> UDP 61702 -> lm_simhub4 bridge -> 30777 (motion + haptic fields, seat-gated)
                                   |-> UDP 61701 -> SimHub plugin "SC Haptics" (raw events, all of them)
                                   '-> add-on taps (optional; ports 61704-61708, 61720-61739)

Every datagram received on 61700 is copied byte-for-byte to the bridge and the plugin, then to
each add-on tap (scm_taps.py: one file per add-on in ~/.config/sc-motion/taps.d, read once at
start). The tap copies are sent last, on their own non-blocking socket: a tap that is slow,
missing or broken can never delay or change the bridge and plugin copies. Tap errors are counted
in the stats line. A problem reading the tap files only means no taps (the line says why).
If the tee stops forwarding, the bridge sees no ev3 and uses its own stale
ramp (0.3 s, then 0.5 s ramp to 0), same as when the game stops sending.
The plugin copy is fire-and-forget: if nothing listens on 61701 the
datagrams are simply dropped, the bridge copy is unaffected.

Env (optional): LM_TEE_IN (61700), LM_TEE_BRIDGE (61702), LM_TEE_PLUGIN (61701);
LM_TEE_TAPS=0 (no add-on copies at all); SC_BRIDGE_CONFIG_DIR (where taps.d is);
all lm_simhub3 / lm_simhub4 env variables apply unchanged.

Usage:
  python3 lm_simhub4t.py [print]     run tee + bridge
  python3 lm_simhub4t.py sim FILE    same as lm_simhub4.py sim FILE (offline)
"""
import os, socket, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lm_simhub3  # noqa: E402  (unchanged module; IN_PORT lives here)
import lm_simhub4 as bridge  # noqa: E402

TEE_IN = int(os.environ.get("LM_TEE_IN", "61700"))
TEE_BRIDGE = int(os.environ.get("LM_TEE_BRIDGE", "61702"))
TEE_PLUGIN = int(os.environ.get("LM_TEE_PLUGIN", "61701"))
HOST = "127.0.0.1"

if len({TEE_IN, TEE_BRIDGE, TEE_PLUGIN, 30777}) != 4:
    sys.exit("LM_TEE_IN, LM_TEE_BRIDGE, LM_TEE_PLUGIN and 30777 must all differ")

stats = {"rx": 0, "bridge_err": 0, "plugin_err": 0, "extra_err": {}}
# Extra copies, sent after the bridge and plugin copies: [(name, port)]. Filled from the tap files
# in main(); the private developer tee adds its watcher port here before calling main().
EXTRA = []


def tee_loop(rx):
    to_bridge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    to_plugin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    to_extra = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    to_extra.setblocking(False)
    extra = [(name, (HOST, port)) for name, port in EXTRA]
    err = stats["extra_err"]
    while True:
        try:
            data, _ = rx.recvfrom(65535)
        except OSError as e:
            print("tee: receive error (%s); tee stopped, bridge will ramp to 0" % e, flush=True)
            return
        stats["rx"] += 1
        try:
            to_bridge.sendto(data, (HOST, TEE_BRIDGE))
        except OSError:
            stats["bridge_err"] += 1
        try:
            to_plugin.sendto(data, (HOST, TEE_PLUGIN))
        except OSError:
            stats["plugin_err"] += 1
        for name, addr in extra:
            try:
                to_extra.sendto(data, addr)
            except OSError:
                err[name] = err.get(name, 0) + 1


def stats_loop():
    while True:
        time.sleep(10.0)
        line = "tee: rx=%d bridge_err=%d plugin_err=%d" % (stats["rx"], stats["bridge_err"], stats["plugin_err"])
        if EXTRA:
            line += " " + " ".join("%s_err=%d" % (n, stats["extra_err"].get(n, 0)) for n, _p in EXTRA)
        print(line, flush=True)


def add_taps():
    """Add the usable tap files to EXTRA. Never raises: a problem means no taps."""
    if os.environ.get("LM_TEE_TAPS") == "0":
        print("tee: add-on taps off (LM_TEE_TAPS=0)", flush=True)
        return
    try:
        import scm_taps
        taps, notes = scm_taps.load()
    except Exception as e:  # noqa: BLE001  the motion path must start without taps
        print("tee: add-on taps not read (%s: %s); running without them" % (type(e).__name__, e), flush=True)
        return
    taken = {TEE_IN, TEE_BRIDGE, TEE_PLUGIN, 30777} | {p for _n, p in EXTRA}
    for t in taps:
        if t["port"] in taken:
            print("tee: tap %s skipped (port %d is already used here)" % (t["name"], t["port"]), flush=True)
            continue
        taken.add(t["port"])
        EXTRA.append((t["name"], t["port"]))
        print("tee: add-on tap %s -> UDP %d" % (t["name"], t["port"]), flush=True)
    for n in notes:
        print("tee: tap file " + n, flush=True)


def simdef():
    path, how = bridge.find_simdef()
    sd = bridge.load_simdef(path)
    sd["how"] = how
    return sd


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "sim":
        if len(sys.argv) < 3 or not os.path.isfile(sys.argv[2]):
            sys.exit("usage: lm_simhub4t.py sim FILE")
        bridge.sim(simdef(), sys.argv[2])
        return
    show = len(sys.argv) > 1 and sys.argv[1] == "print"
    sd = simdef()    # before binding: a bad .simdef stops here
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind((HOST, TEE_IN))
    except OSError as e:
        sys.exit("cannot bind UDP %d (%s). Another process holds it: run  ss -ulpn | grep %d  and kill that pid" % (TEE_IN, e, TEE_IN))
    print("tee: UDP %d -> bridge %d + plugin %d" % (TEE_IN, TEE_BRIDGE, TEE_PLUGIN), flush=True)
    add_taps()
    threading.Thread(target=tee_loop, args=(rx,), daemon=True).start()
    if show:
        threading.Thread(target=stats_loop, daemon=True).start()
    lm_simhub3.IN_PORT = TEE_BRIDGE      # the bridge listens behind the tee
    bridge.run(sd, show)


if __name__ == "__main__":
    main()
