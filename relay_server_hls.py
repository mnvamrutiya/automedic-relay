import os
import time
import threading
from pathlib import Path
from flask import Flask, request, Response, jsonify, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

PUSH_SECRET  = os.environ.get("PUSH_SECRET", "change-me")
HLS_STORE    = Path("/tmp/hls")
HLS_STORE.mkdir(parents=True, exist_ok=True)
MAX_SEGMENTS = 20

state_lock       = threading.Lock()
cached_status    = {}
pending_override = None


def require_push_auth():
    if request.headers.get("X-Push-Secret", "") != PUSH_SECRET:
        abort(403, "Bad push secret")


@app.route("/hls/<filename>", methods=["PUT"])
def hls_put(filename):
    require_push_auth()
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    if not safe:
        abort(400, "Bad filename")
    (HLS_STORE / safe).write_bytes(request.get_data())
    if safe.endswith(".ts"):
        segs = sorted(HLS_STORE.glob("*.ts"), key=lambda p: p.stat().st_mtime)
        for old in segs[:-MAX_SEGMENTS]:
            try:
                old.unlink()
            except Exception:
                pass
    return "", 204


@app.route("/hls/<filename>", methods=["GET"])
def hls_get(filename):
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    dest = HLS_STORE / safe
    if not dest.exists():
        abort(404)
    if safe.endswith(".m3u8"):
        ct = "application/vnd.apple.mpegurl"
    elif safe.endswith(".ts"):
        ct = "video/mp2t"
    else:
        ct = "application/octet-stream"
    return Response(
        dest.read_bytes(),
        content_type=ct,
        headers={"Cache-Control": "no-cache, no-store", "Access-Control-Allow-Origin": "*"},
    )


@app.route("/api/push_status", methods=["POST"])
def push_status():
    require_push_auth()
    with state_lock:
        cached_status.clear()
        cached_status.update(request.get_json(force=True) or {})
        cached_status["_relay_ts"] = time.time()
    return "", 204


@app.route("/api/status", methods=["GET"])
def api_status():
    with state_lock:
        data = dict(cached_status)
    age = time.time() - data.get("_relay_ts", 0) if data.get("_relay_ts") else 999
    data["relay_online"] = age < 5.0
    if "stats" not in data:
        data["stats"] = {"fps": 0, "yolo_detections": 0, "candidates": 0,
                         "alerts": 0, "rover_state": "WAITING FOR JETSON"}
    return jsonify(data)


@app.route("/api/override", methods=["POST"])
def api_override():
    global pending_override
    body = request.get_json(force=True) or {}
    with state_lock:
        pending_override = {
            "active": bool(body.get("active", False)),
            "command": str(body.get("command", "STOP")),
        }
    return jsonify({"ok": True, "queued": True})


@app.route("/api/override/poll", methods=["GET"])
def override_poll():
    require_push_auth()
    global pending_override
    with state_lock:
        cmd = pending_override
        pending_override = None
    return jsonify(cmd or {"active": False, "command": None})


@app.route("/health")
def health():
    segs = len(list(HLS_STORE.glob("*.ts")))
    age = time.time() - cached_status.get("_relay_ts", 0) if cached_status.get("_relay_ts") else None
    return jsonify({"ok": True, "hls_segments": segs, "status_age_sec": age})


@app.route("/")
def index():
    return jsonify({"ok": True, "service": "automedic-relay-hls"})
