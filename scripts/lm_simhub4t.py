#!/usr/bin/env python3
"""lm_simhub4t.py - lm_simhub4 bridge (v3 motion + Motion-haptics fields) + raw-event tee.

New file; a copy of lm_simhub3t.py that runs lm_simhub4 instead of lm_simhub3 (which
stays the fallback). Packet layout comes from SimHub's "Star Citizen.simdef" (see
lm_simhub4.py). Where the bridge listens:

  capture server -> UDP 61700 -> [tee in this process]
                                   |-> UDP 61702 -> lm_simhub4 bridge -> 30777 (motion + haptic fields, seat-gated)
                                   '-> UDP 61701 -> SimHub plugin "SC Haptics" (raw events, all of them)

Every datagram received on 61700 is copied byte-for-byte to both ports.
If the tee stops forwarding, the bridge sees no ev3 and uses its own stale
ramp (0.3 s, then 0.5 s ramp to 0), same as when the game stops sending.
The plugin copy is fire-and-forget: if nothing listens on 61701 the
datagrams are simply dropped, the bridge copy is unaffected.

Env (optional): LM_TEE_IN (61700), LM_TEE_BRIDGE (61702), LM_TEE_PLUGIN (61701);
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

stats = {"rx": 0, "bridge_err": 0, "plugin_err": 0}


def tee_loop(rx):
    to_bridge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    to_plugin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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


def stats_loop():
    while True:
        time.sleep(10.0)
        print("tee: rx=%d bridge_err=%d plugin_err=%d" % (stats["rx"], stats["bridge_err"], stats["plugin_err"]), flush=True)


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
    threading.Thread(target=tee_loop, args=(rx,), daemon=True).start()
    if show:
        threading.Thread(target=stats_loop, daemon=True).start()
    lm_simhub3.IN_PORT = TEE_BRIDGE      # the bridge listens behind the tee
    bridge.run(sd, show)


if __name__ == "__main__":
    main()
