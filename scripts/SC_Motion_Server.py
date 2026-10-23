"""SC_Motion_Server.py - capture server (formerly lm_server2.py): receives the game's motion
telemetry on gRPC TLS 127.0.0.1:61666 and forwards every posted event as one JSON line per UDP
datagram to 127.0.0.1:61700 (env LM_UDP_PORT).

Message schema: motion_desc.bin, written by hand in tools/motion_schema.py. Looked for in this
order: env SC_MOTION_DESC; next to this script (following a symlink into the repository);
next to the symlink itself; ~/motion_desc.bin.
Certificate pair: ~/lm.crt and ~/lm.key (sc_lmcert.py).
"""
import grpc, os, sys, time, json, socket, threading
from concurrent import futures
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, text_format
H = os.path.expanduser("~")
UDP = ("127.0.0.1", int(os.environ.get("LM_UDP_PORT", "61700")))
os.makedirs(H + "/lm_msgs", exist_ok=True)


def find_desc():
    here = os.path.abspath(__file__)
    cands = [os.environ.get("SC_MOTION_DESC"),
             os.path.join(os.path.dirname(os.path.realpath(here)), "motion_desc.bin"),
             os.path.join(os.path.dirname(here), "motion_desc.bin"),
             os.path.join(H, "motion_desc.bin")]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    sys.exit("motion_desc.bin not found (looked in: %s). It is in the repository's scripts/ folder; "
             "run the installer (step 5) or set SC_MOTION_DESC." % ", ".join(dict.fromkeys(p for p in cands if p)))


raw = open(find_desc(), "rb").read()
fdp = descriptor_pb2.FileDescriptorProto.FromString(raw)
pool = descriptor_pool.DescriptorPool()
pool.AddSerializedFile(raw)
PKG = fdp.package
def M(n):
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(PKG + "." + n))
IdReq = M("IdentificationRequest")
IdResp = M("IdentificationResponse")
Req = M("LiveMotionRequest")
Resp = M("LiveMotionResponse")
EVN = {0: "Global", 1: "EnterVehicleConfig", 2: "LeaveVehicle", 3: "VehicleFrame", 4: "Vibration", 5: "Weapon", 6: "ExplosiveOrdnance", 7: "VehicleImpact", 8: "VehicleExplosionImpact", 9: "Decoupled", 10: "PlayerRole", 11: "Turret", 12: "Actor", 13: "Recoil", 14: "Heartbeat", 15: "ForceReaction", 16: "Footstep", 17: "AudioHaptic", 18: "GroundVehicle", 19: "Throw"}
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
defs = {}
counts = {}
pcounts = {}
lock = threading.Lock()
LOG = H + "/lm_stream.log"
def logline(s):
    with open(LOG, "a") as f:
        print(s, file=f)
def val(ef):
    w = ef.WhichOneof("type")
    if w is None:
        return None
    v = getattr(ef, w)
    if w.endswith("XYZ"):
        return [v.x, v.y, v.z]
    if w.startswith("dataQuad"):
        return [v.frontLeft, v.frontRight, v.backLeft, v.backRight]
    if w == "dataWideString":
        return v.hex()
    return v
def handle_post(info):
    key = info.eventKey
    ms = [x[1] for x in defs.get(key, [])]
    for ed in info.eventData:
        vals = [val(ef) for ef in ed.eventField]
        pkt = {"t": round(time.time(), 4), "ev": key, "name": EVN.get(key, "?"), "m": ms, "v": vals}
        sock.sendto(json.dumps(pkt).encode(), UDP)
        with lock:
            pcounts[key] = pcounts.get(key, 0) + 1
            n = pcounts[key]
        if n <= 3:
            logline("POST " + json.dumps(pkt))
def ident(req, ctx):
    print("GetIdentification:", text_format.MessageToString(req, as_one_line=True), flush=True)
    r = IdResp()
    r.server_id = "lm-sc-bridge"
    r.server_name = "D-BOX Motion Engine"
    r.server_version = "6.12.0"
    r.supported_client_types.append(2)
    r.single_client_mode = False
    r.current_client_count = 0
    return r
def stream(it, ctx):
    print("stream opened", flush=True)
    for req in it:
        name = "unknown"
        for o in Req.DESCRIPTOR.oneofs:
            w = req.WhichOneof(o.name)
            if w:
                name = w
        with lock:
            counts[name] = counts.get(name, 0) + 1
            n = counts[name]
        if name == "register_event":
            ed = req.register_event.eventDefinition
            defs[ed.eventKey] = [(f.type, f.meaning, f.offset) for f in ed.fieldDef]
        if name.startswith("post_event"):
            handle_post(getattr(req, name).eventInfo)
        else:
            logline("=== %s #%d t=%.3f" % (name, n, time.time()))
            logline(text_format.MessageToString(req))
            if n <= 2:
                print("stream req:", name, "#%d" % n, flush=True)
        resp = Resp()
        resp.request_id = req.request_id
        resp.timestamp = int(time.time() * 1000)
        resp.result_code = 0
        if name == "heartbeat":
            resp.heartbeat_response.server_timestamp = int(time.time() * 1000)
            resp.heartbeat_response.client_timestamp = req.heartbeat.client_timestamp
            resp.heartbeat_response.connected_clients = 1
        elif name.startswith("post_events"):
            resp.post_events_reply.SetInParent()
        elif name.startswith("post_event"):
            pass
        else:
            resp.reply.SetInParent()
        yield resp
    print("stream closed; posts per event:", pcounts, flush=True)
ser = lambda m: m.SerializeToString()
handlers = grpc.method_handlers_generic_handler(PKG + ".LiveMotion", {
    "GetIdentification": grpc.unary_unary_rpc_method_handler(ident, request_deserializer=IdReq.FromString, response_serializer=ser),
    "LiveMotionStream": grpc.stream_stream_rpc_method_handler(stream, request_deserializer=Req.FromString, response_serializer=ser),
})
key = open(H + "/lm.key", "rb").read()
crt = open(H + "/lm.crt", "rb").read()
server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
server.add_generic_rpc_handlers((handlers,))
server.add_secure_port("127.0.0.1:61666", grpc.ssl_server_credentials([(key, crt)]))
server.start()
print("listening on 127.0.0.1:61666, forwarding UDP to", UDP, flush=True)
try:
    server.wait_for_termination()
except KeyboardInterrupt:
    server.stop(0)
