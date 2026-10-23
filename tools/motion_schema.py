#!/usr/bin/env python3
"""motion_schema.py - the message schema SC_Motion_Server.py uses, written out by hand, and the
generator for scripts/motion_desc.bin (a serialized protobuf FileDescriptorProto).

  python3 tools/motion_schema.py build         write scripts/motion_desc.bin from the table below
  python3 tools/motion_schema.py check         exit 0 if scripts/motion_desc.bin == a fresh build
  python3 tools/motion_schema.py compare FILE  compare with another FileDescriptorProto (read as
                                               data only), e.g. a locally extracted descriptor

Why the package name is "dbox.proto.v1": the game's connector calls the gRPC paths
/dbox.proto.v1.LiveMotion/GetIdentification and /dbox.proto.v1.LiveMotion/LiveMotionStream.
A server must answer on those paths, so the package and service names cannot change. Field
numbers and types must match what the connector sends; names are only for our own code.

Left out on purpose: the two credential fields of InitializeParam. The server never needs them,
so with this schema they are not decoded and not written to any log (protobuf keeps them as
unknown bytes and drops them). See docs/PUBLISHING.md.

Needs python-protobuf (the server needs it anyway). Output is deterministic: the same table
always gives the same bytes, so `check` can be part of the tests.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "scripts", "motion_desc.bin")

FILE_NAME = "motion.proto"
PACKAGE = "dbox.proto.v1"      # wire path, see above
SERVICE = "LiveMotion"         # wire path, see above

# Field kinds. "msg:X" / "enum:X" refer to a message / enum in this file. A leading "*" means
# repeated. A trailing "@group" puts the field in that oneof group of its message.
# Each message: name -> list of (field name, number, kind)
MESSAGES = [
    ("ClientInfo", [
        ("client_id", 1, "string"),
        ("client_type", 2, "enum:ClientType"),
    ]),
    ("IdentificationRequest", [
        ("client_info", 1, "msg:ClientInfo"),
        ("client_version", 2, "string"),
    ]),
    ("IdentificationResponse", [
        ("server_id", 1, "string"),
        ("server_name", 2, "string"),
        ("server_version", 3, "string"),
        ("supported_client_types", 7, "*enum:ClientType"),
        ("single_client_mode", 8, "bool"),
        ("current_client_count", 9, "int32"),
    ]),
    ("LiveMotionRequest", [
        ("client_info", 1, "msg:ClientInfo"),
        ("request_id", 3, "uint64"),
        ("timestamp", 4, "uint64"),
        ("initialize", 10, "msg:InitializeParam@request_type"),
        ("terminate", 11, "msg:TerminateParam@request_type"),
        ("open", 12, "msg:OpenParam@request_type"),
        ("close", 13, "msg:CloseParam@request_type"),
        ("start", 14, "msg:StartParam@request_type"),
        ("stop", 15, "msg:StopParam@request_type"),
        ("register_event", 16, "msg:RegisterEventParam@request_type"),
        ("set_open_extra_sessions", 17, "msg:SetOpenExtraSessionsParam@request_type"),
        ("reset_state", 18, "msg:ResetStateParam@request_type"),
        ("post_event", 19, "msg:PostEventParam@request_type"),
        ("post_event_to_session", 20, "msg:PostEventToSessionParam@request_type"),
        ("post_events", 21, "msg:PostEventsParam@request_type"),
        ("post_events_to_session", 22, "msg:PostEventsToSessionParam@request_type"),
        ("heartbeat", 23, "msg:HeartbeatParam@request_type"),
    ]),
    ("LiveMotionResponse", [
        ("client_id", 1, "string"),
        ("request_id", 2, "uint64"),
        ("timestamp", 3, "uint64"),
        ("result_code", 4, "int32"),
        ("message", 5, "string"),
        ("reply", 10, "msg:Reply@response_type"),
        ("post_events_reply", 11, "msg:PostEventsReply@response_type"),
        ("heartbeat_response", 12, "msg:HeartbeatResponse@response_type"),
        ("server_notification", 13, "msg:ServerNotification@response_type"),
    ]),
    ("Reply", [
        ("liveMotionResult", 1, "int32"),
    ]),
    ("PostEventsReply", [
        ("liveMotionResult", 1, "int32"),
        ("eventBufferedCount", 2, "uint32"),
    ]),
    ("HeartbeatResponse", [
        ("server_timestamp", 1, "uint64"),
        ("client_timestamp", 2, "uint64"),
        ("connected_clients", 3, "int32"),
    ]),
    ("ServerNotification", [
        ("notification_type", 1, "string"),
        ("affected_client_id", 2, "string"),
        ("details", 3, "string"),
    ]),
    # Event registration: the game describes each event's fields once.
    ("EventDefinition", [
        ("eventKey", 1, "uint32"),
        ("eventMeaning", 2, "uint32"),
        ("fieldCount", 3, "uint32"),
        ("fieldAddress", 4, "uint64"),
        ("fieldDef", 5, "*msg:FieldDefinition"),
    ]),
    ("FieldDefinition", [
        ("type", 1, "uint32"),
        ("flags", 2, "uint32"),
        ("meaning", 3, "uint32"),
        ("offset", 4, "uint32"),
    ]),
    # Value containers
    ("XYZ_StructFloat32", [("x", 1, "float"), ("y", 2, "float"), ("z", 3, "float")]),
    ("XYZ_StructFloat64", [("x", 1, "double"), ("y", 2, "double"), ("z", 3, "double")]),
    ("QuadInt32", [("frontLeft", 1, "int32"), ("frontRight", 2, "int32"),
                   ("backLeft", 3, "int32"), ("backRight", 4, "int32")]),
    ("QuadFloat32", [("frontLeft", 1, "float"), ("frontRight", 2, "float"),
                     ("backLeft", 3, "float"), ("backRight", 4, "float")]),
    ("QuadFloat64", [("frontLeft", 1, "double"), ("frontRight", 2, "double"),
                     ("backLeft", 3, "double"), ("backRight", 4, "double")]),
    # One value of a posted event; exactly one member is set.
    ("EventField", [
        ("dataInt32", 1, "int32@type"),
        ("dataInt64", 2, "int64@type"),
        ("dataFloat32", 3, "float@type"),
        ("dataFloat64", 4, "double@type"),
        ("dataString", 5, "string@type"),
        ("dataWideString", 6, "bytes@type"),
        ("dataFloat32XYZ", 7, "msg:XYZ_StructFloat32@type"),
        ("dataFloat64XYZ", 8, "msg:XYZ_StructFloat64@type"),
        ("dataQuadInt32", 9, "msg:QuadInt32@type"),
        ("dataQuadFloat32", 10, "msg:QuadFloat32@type"),
        ("dataQuadFloat64", 11, "msg:QuadFloat64@type"),
    ]),
    ("EventData", [
        ("eventField", 1, "*msg:EventField"),
    ]),
    ("EventInfo", [
        ("eventKey", 1, "uint32"),
        ("eventData", 2, "*msg:EventData"),
        ("size", 3, "uint64"),
        ("count", 4, "uint64"),
    ]),
    # Request parameters
    ("InitializeParam", [
        ("appVersion", 2, "uint32"),   # fields 1 and 3 (credentials) left out on purpose
    ]),
    ("TerminateParam", []),
    ("OpenParam", []),
    ("CloseParam", []),
    ("StartParam", []),
    ("StopParam", []),
    ("ResetStateParam", []),
    ("RegisterEventParam", [
        ("eventDefinition", 1, "msg:EventDefinition"),
    ]),
    ("PostEventParam", [
        ("eventInfo", 1, "msg:EventInfo"),
    ]),
    ("PostEventToSessionParam", [
        ("targetSession", 1, "uint32"),
        ("eventInfo", 2, "msg:EventInfo"),
    ]),
    ("SetOpenExtraSessionsParam", [
        ("additionalSessions", 1, "uint32"),
    ]),
    ("PostEventsParam", [
        ("eventInfo", 1, "msg:EventInfo"),
        ("eventBufferedCount", 2, "uint32"),
    ]),
    ("PostEventsToSessionParam", [
        ("targetSession", 1, "uint32"),
        ("eventInfo", 2, "msg:EventInfo"),
        ("eventBufferedCount", 3, "uint32"),
    ]),
    ("HeartbeatParam", [
        ("client_timestamp", 1, "uint64"),
    ]),
]

ENUMS = [
    ("ClientType", [("Default", 0), ("UniversalHaptiCode", 1), ("LiveMotionGame", 2)]),
]

# (method, request message, response message, client streaming, server streaming)
METHODS = [
    ("GetIdentification", "IdentificationRequest", "IdentificationResponse", False, False),
    ("LiveMotionStream", "LiveMotionRequest", "LiveMotionResponse", True, True),
]

# Fields another descriptor may have that this one leaves out on purpose (see the docstring).
OMITTED = {("InitializeParam", 1), ("InitializeParam", 3)}

SCALARS = ("double", "float", "int64", "uint64", "int32", "bool", "string", "bytes", "uint32")


def build():
    """FileDescriptorProto for the table above."""
    from google.protobuf import descriptor_pb2 as d
    F = d.FieldDescriptorProto
    fdp = d.FileDescriptorProto(name=FILE_NAME, package=PACKAGE, syntax="proto3")
    for mname, fields in MESSAGES:
        m = fdp.message_type.add(name=mname)
        groups = []
        for fname, number, kind in fields:
            group = None
            if "@" in kind:
                kind, group = kind.split("@", 1)
            label = F.LABEL_OPTIONAL
            if kind.startswith("*"):
                kind, label = kind[1:], F.LABEL_REPEATED
            f = m.field.add(name=fname, number=number, label=label)
            if kind.startswith("msg:"):
                f.type, f.type_name = F.TYPE_MESSAGE, ".%s.%s" % (PACKAGE, kind[4:])
            elif kind.startswith("enum:"):
                f.type, f.type_name = F.TYPE_ENUM, ".%s.%s" % (PACKAGE, kind[5:])
            elif kind in SCALARS:
                f.type = getattr(F, "TYPE_" + kind.upper())
            else:
                raise ValueError("unknown kind %r in %s.%s" % (kind, mname, fname))
            if group:
                if group not in groups:
                    groups.append(group)
                    m.oneof_decl.add(name=group)
                f.oneof_index = groups.index(group)
    for ename, values in ENUMS:
        e = fdp.enum_type.add(name=ename)
        for vname, num in values:
            e.value.add(name=vname, number=num)
    s = fdp.service.add(name=SERVICE)
    for name, req, resp, cs, ss in METHODS:
        mm = s.method.add(name=name, input_type=".%s.%s" % (PACKAGE, req),
                          output_type=".%s.%s" % (PACKAGE, resp))
        if cs:
            mm.client_streaming = True
        if ss:
            mm.server_streaming = True
    return fdp


def build_bytes():
    raw = build().SerializeToString(deterministic=True)
    # must load into a fresh pool, as the server does
    from google.protobuf import descriptor_pool
    descriptor_pool.DescriptorPool().AddSerializedFile(raw)
    return raw


def _fields(fdp):
    """{(message, number): (label, type, type_name without package, oneof name or '')}"""
    out = {}
    pre = "." + fdp.package + "."
    for m in fdp.message_type:
        for f in m.field:
            oneof = m.oneof_decl[f.oneof_index].name if f.HasField("oneof_index") else ""
            tn = f.type_name[len(pre):] if f.type_name.startswith(pre) else f.type_name
            out[(m.name, f.number)] = (f.label, f.type, tn, oneof)
    return out


def compare(ours, theirs):
    """List of problems; empty = compatible. Every field we define must exist in theirs with the
    same number, label, type and oneof group; fields only they have must be in OMITTED."""
    probs = []
    if ours.package != theirs.package:
        probs.append("package %s != %s" % (ours.package, theirs.package))
    a, b = _fields(ours), _fields(theirs)
    for k, v in sorted(a.items()):
        if k not in b:
            probs.append("%s field %d: not in the other descriptor" % k)
        elif v != b[k]:
            probs.append("%s field %d: ours %s, theirs %s" % (k[0], k[1], v, b[k]))
    for k in sorted(set(b) - set(a)):
        if k not in OMITTED:
            probs.append("%s field %d: only in the other descriptor (not omitted on purpose)" % k)
    ea = {(e.name, v.name, v.number) for e in ours.enum_type for v in e.value}
    eb = {(e.name, v.name, v.number) for e in theirs.enum_type for v in e.value}
    if ea != eb:
        probs.append("enum values differ: ours-only %s, theirs-only %s" % (sorted(ea - eb), sorted(eb - ea)))
    def svc(fdp):
        return {(s.name, m.name, m.input_type.rsplit(".", 1)[-1], m.output_type.rsplit(".", 1)[-1],
                 m.client_streaming, m.server_streaming) for s in fdp.service for m in s.method}
    if svc(ours) != svc(theirs):
        probs.append("service methods differ: ours %s, theirs %s" % (sorted(svc(ours)), sorted(svc(theirs))))
    return probs


def main(a):
    if a[1:] == ["build"]:
        raw = build_bytes()
        with open(OUT + ".tmp", "wb") as f:
            f.write(raw)
        os.replace(OUT + ".tmp", OUT)
        import hashlib
        print("wrote %s (%d bytes, sha256 %s)" % (OUT, len(raw), hashlib.sha256(raw).hexdigest()))
        return 0
    if a[1:] == ["check"]:
        raw = build_bytes()
        have = open(OUT, "rb").read() if os.path.isfile(OUT) else b""
        if have == raw:
            print("PASS motion_desc.bin matches the table (%d bytes)" % len(raw))
            return 0
        print("FAIL motion_desc.bin differs from the table: run python3 tools/motion_schema.py build")
        return 2
    if len(a) == 3 and a[1] == "compare":
        from google.protobuf import descriptor_pb2 as d
        theirs = d.FileDescriptorProto.FromString(open(a[2], "rb").read())  # data only
        probs = compare(build(), theirs)
        for p in probs:
            print("DIFF " + p)
        n = len(_fields(build()))
        if probs:
            print("FAIL %d differences" % len(probs))
            return 2
        print("PASS all %d fields match; left out on purpose: %d" % (
            n, len(OMITTED & set(_fields(theirs)))))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
