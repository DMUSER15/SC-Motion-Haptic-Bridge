"""scm_ports.py - the one table of local ports the SC Motion Haptic Bridge uses.

Everything listens on 127.0.0.1 only. The launchers, installers and verifiers take their port
numbers from here. The programs in the motion path (capture server, tee, bridge, plugin) and the
test tools keep their own numbers in their own files, because those files are released
and checked byte for byte; tests/test_ports.py fails when one of them disagrees with this table.

Add-ons (contract v1) get a copy of the telemetry on a tap port: 61704-61708 and 61720-61739.
An add-on never uses a port of the table below.

  python3 setup/common/scm_ports.py        print the table
"""
import sys

HOST = "127.0.0.1"

# name: (port, protocol, who listens, what it is)
PORTS = {
    "capture":       (61666, "tcp", "SC_Motion_Server.py", "capture server: gRPC TLS, the game's D-BOX connector connects here"),
    "tee":           (61700, "udp", "lm_simhub4t.py / lm_simhub3t.py", "telemetry in: one JSON post per datagram from the capture server"),
    "plugin":        (61701, "udp", "SimHub plugin SC Haptics", "the tee's copy for the shaker effects"),
    "bridge":        (61702, "udp", "lm_simhub4.py / lm_simhub3.py", "the tee's copy for the motion bridge"),
    "reserved_1":    (61703, "udp", "reserved", "kept free for test tools; never an add-on port"),
    "reserved_2":    (61709, "udp", "reserved", "kept free for test tools; never an add-on port"),
    "reserved_3":    (61710, "udp", "reserved", "kept free for test tools; never an add-on port"),
    "simhub":        (30777, "udp", "SimHub, External Sim \"Star Citizen\"", "motion packets from the bridge"),
}
ADDON_RANGES = ((61704, 61708), (61720, 61739))
ADDON_DEFAULT = 61704          # the first add-on port
MAX_TAPS = 8

CAPTURE_TCP = PORTS["capture"][0]
TEE_UDP = PORTS["tee"][0]
PLUGIN_UDP = PORTS["plugin"][0]
BRIDGE_UDP = PORTS["bridge"][0]
SIMHUB_UDP = PORTS["simhub"][0]

RESERVED = frozenset(p for p, _proto, _who, _what in PORTS.values())


def is_addon_port(port):
    return isinstance(port, int) and not isinstance(port, bool) and any(lo <= port <= hi for lo, hi in ADDON_RANGES)


def tap_port_problem(port, taken=()):
    """Why an add-on may not use this tap port ("" = it may)."""
    if not isinstance(port, int) or isinstance(port, bool):
        return "the port must be a whole number"
    if port in RESERVED:
        name = next(n for n, v in PORTS.items() if v[0] == port)
        return "port %d is the bridge's own (%s)" % (port, name)
    if not is_addon_port(port):
        return "port %d is outside the add-on ranges %s" % (port, ", ".join("%d-%d" % r for r in ADDON_RANGES))
    if port in taken:
        return "port %d is already used by another tap" % port
    return ""


def table():
    rows = ["%-6s %-4s %-14s %s" % ("port", "", "name", "listener: use")]
    for name, (port, proto, who, what) in sorted(PORTS.items(), key=lambda kv: kv[1][0]):
        rows.append("%-6d %-4s %-14s %s: %s" % (port, proto, name, who, what))
    for lo, hi in ADDON_RANGES:
        rows.append("%d-%d udp add-on taps%s" % (lo, hi, " (first: %d)" % ADDON_DEFAULT if lo <= ADDON_DEFAULT <= hi else ""))
    return "\n".join(rows)


if __name__ == "__main__":
    print(table())
    sys.exit(0)
