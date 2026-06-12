#!/usr/bin/env python3
"""
YTDL backend — queue-based YouTube downloader.
H.264/AAC MP4, 640×400 target (Meta Ray-Ban Display).
iPhone sync via pymobiledevice3 (HouseArrest → LiftLens Documents).
Run: python3 server.py
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import warnings
warnings.filterwarnings("ignore")

from flask import Flask, request, Response, send_from_directory, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
YTDLP      = os.path.expanduser("~/Library/Python/3.13/bin/yt-dlp")
STATE_FILE = os.path.expanduser("~/.ytdl_state.json")
LIFTLENS_BUNDLE_ID = "com.santiarano.liftlens.display"

# ── State ─────────────────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"download_dir": os.path.expanduser("~/Downloads"), "synced": []}

def _save_state():
    with state_lock:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({"download_dir": DOWNLOAD_DIR, "synced": synced_files}, f)
        except Exception:
            pass

_state      = _load_state()
DOWNLOAD_DIR = _state["download_dir"]
synced_files = _state.get("synced", [])   # basenames already synced to iPhone
state_lock   = threading.Lock()

# ── Queue ─────────────────────────────────────────────────────────────────────

queue      = []   # list of dicts
queue_lock = threading.Lock()
sse_clients = []  # list of queue.Queue for SSE

def _notify_clients():
    snapshot = _queue_snapshot()
    for q in list(sse_clients):
        try:
            q.put_nowait(snapshot)
        except Exception:
            pass

def _queue_snapshot():
    with queue_lock:
        return json.dumps({"type": "queue", "items": queue, "download_dir": DOWNLOAD_DIR})

def _set_item(item_id, **kwargs):
    with queue_lock:
        for item in queue:
            if item["id"] == item_id:
                item.update(kwargs)
                break
    _notify_clients()

def _add_items(urls):
    added = []
    with queue_lock:
        for url in urls:
            url = url.strip()
            if not url:
                continue
            item = {
                "id":       str(uuid.uuid4())[:8],
                "url":      url,
                "status":   "pending",
                "progress": 0,
                "filename": None,
                "error":    None,
            }
            queue.append(item)
            added.append(item)
    _notify_clients()
    return added

# ── Worker ────────────────────────────────────────────────────────────────────

def _worker():
    while True:
        item = None
        with queue_lock:
            for i in queue:
                if i["status"] == "pending":
                    i["status"] = "downloading"
                    item = i
                    break
        if item:
            _process(item)
        else:
            time.sleep(0.5)

def _process(item):
    _set_item(item["id"], status="downloading", progress=0, error=None)
    _notify_clients()

    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore"

    # Meta Ray-Ban Display: 640×400, H.264 + AAC, MP4 with faststart for streaming
    cmd = [
        YTDLP, "--newline", "--no-playlist",
        "-f", (
            "bestvideo[vcodec^=avc1][height<=400][width<=640]+bestaudio[ext=m4a]"
            "/bestvideo[vcodec^=avc][height<=400][width<=640]+bestaudio[ext=m4a]"
            "/bestvideo[height<=400]+bestaudio"
            "/best[height<=400]"
        ),
        "--merge-output-format", "mp4",
        # Always encode to H.264+AAC for guaranteed iOS/glasses playback
        "--postprocessor-args",
            "ffmpeg:-c:v libx264 -profile:v main -level 4.0 "
            "-c:a aac -b:a 128k -movflags +faststart -preset fast -crf 23",
        "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        item["url"],
    ]

    pct_re   = re.compile(r'\[download\]\s+([\d.]+)%')
    dest_re  = re.compile(r'\[(?:download|ExtractAudio)\] Destination:\s+(.+)')
    merge_re = re.compile(r'\[Merger\] Merging formats into "(.+)"')
    done_re  = re.compile(r'\[download\] (.+) has already been downloaded')

    last_file = None

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env
    )

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue

        m_pct   = pct_re.search(line)
        m_dest  = dest_re.search(line)
        m_merge = merge_re.search(line)
        m_done  = done_re.search(line)

        if m_dest or m_merge:
            last_file = (m_dest or m_merge).group(1).strip()
            _set_item(item["id"], filename=os.path.basename(last_file))
        elif m_done:
            last_file = m_done.group(1)
            _set_item(item["id"], filename=os.path.basename(last_file), progress=100)
        elif m_pct:
            pct = round(float(m_pct.group(1)))
            _set_item(item["id"], progress=pct)

    proc.wait()
    if proc.returncode == 0:
        _set_item(item["id"], status="done", progress=100,
                  filename=os.path.basename(last_file) if last_file else item["filename"])
    else:
        _set_item(item["id"], status="error", error="yt-dlp exited with error")

threading.Thread(target=_worker, daemon=True).start()

# ── iPhone sync ───────────────────────────────────────────────────────────────

sync_status = {"running": False, "results": [], "error": None}
sync_lock   = threading.Lock()

def _sync_iphone():
    global synced_files
    with sync_lock:
        sync_status["running"] = True
        sync_status["results"] = []
        sync_status["error"]   = None

    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService

        lockdown = create_using_usbmux()
        device_name = lockdown.get_value("", "DeviceName") or "iPhone"

        service = HouseArrestService(lockdown=lockdown, bundle_id=LIFTLENS_BUNDLE_ID)
        afc = service.documents_afc()

        # Existing files on device
        try:
            existing = set(afc.listdir("/"))
        except Exception:
            existing = set()

        results = []
        local_files = [
            f for f in os.listdir(DOWNLOAD_DIR)
            if f.lower().endswith((".mp4", ".m4v", ".mov"))
        ]

        for filename in local_files:
            if filename in existing:
                results.append({"file": filename, "status": "already_on_device"})
                continue
            if filename in synced_files:
                results.append({"file": filename, "status": "already_synced"})
                continue

            local_path = os.path.join(DOWNLOAD_DIR, filename)
            try:
                with open(local_path, "rb") as fh:
                    afc.set_file_contents(f"/{filename}", fh.read())
                with state_lock:
                    if filename not in synced_files:
                        synced_files.append(filename)
                _save_state()
                results.append({"file": filename, "status": "synced"})
            except Exception as e:
                results.append({"file": filename, "status": "error", "detail": str(e)})

        with sync_lock:
            sync_status["running"] = False
            sync_status["results"] = results
            sync_status["device"]  = device_name

    except Exception as e:
        with sync_lock:
            sync_status["running"] = False
            sync_status["error"]   = str(e)

# ── Helpers ───────────────────────────────────────────────────────────────────

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(SCRIPT_DIR, "index.html")

@app.route("/serve/<path:filename>")
def serve_video(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, conditional=True)

# -- Queue --

@app.route("/queue", methods=["GET"])
def get_queue():
    return jsonify({"items": queue, "download_dir": DOWNLOAD_DIR})

@app.route("/queue/add", methods=["POST"])
def queue_add():
    data = request.get_json(force=True)
    raw  = data.get("urls", "")
    urls = [u.strip() for u in re.split(r"[\n,]+", raw) if u.strip()]
    added = _add_items(urls)
    return jsonify({"added": len(added)})

@app.route("/queue/<item_id>", methods=["DELETE"])
def queue_delete(item_id):
    with queue_lock:
        before = len(queue)
        queue[:] = [i for i in queue if i["id"] != item_id or i["status"] == "downloading"]
    _notify_clients()
    return jsonify({"ok": True})

@app.route("/queue/clear", methods=["POST"])
def queue_clear():
    with queue_lock:
        queue[:] = [i for i in queue if i["status"] == "downloading"]
    _notify_clients()
    return jsonify({"ok": True})

@app.route("/queue/stream")
def queue_stream():
    import queue as Q
    client_q = Q.Queue()
    sse_clients.append(client_q)
    # Send current state immediately
    client_q.put_nowait(_queue_snapshot())

    def generate():
        try:
            while True:
                try:
                    data = client_q.get(timeout=15)
                except Q.Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"data: {data}\n\n"
        finally:
            try:
                sse_clients.remove(client_q)
            except ValueError:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

# -- Settings --

@app.route("/set-dir", methods=["POST"])
def set_dir():
    global DOWNLOAD_DIR
    data = request.get_json(force=True)
    path = data.get("path", "").strip()
    if not path or not os.path.isabs(path):
        return jsonify({"error": "Invalid path"}), 400
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    DOWNLOAD_DIR = path
    _save_state()
    _notify_clients()
    return jsonify({"path": DOWNLOAD_DIR})

@app.route("/browse", methods=["POST"])
def browse():
    result = subprocess.run(
        ["osascript", "-e",
         'POSIX path of (choose folder with prompt "Select download folder:")'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return jsonify({"cancelled": True})
    path = result.stdout.strip()
    return jsonify({"path": path})

# -- iPhone sync --

@app.route("/sync-iphone", methods=["POST"])
def sync_iphone():
    if sync_status["running"]:
        return jsonify({"error": "Sync already running"}), 409
    threading.Thread(target=_sync_iphone, daemon=True).start()
    return jsonify({"started": True})

@app.route("/sync-iphone/status")
def sync_iphone_status():
    with sync_lock:
        return jsonify(dict(sync_status))

# -- File listing --

@app.route("/files")
def list_files():
    try:
        files = [
            f for f in os.listdir(DOWNLOAD_DIR)
            if f.lower().endswith((".mp4", ".m4v", ".mov"))
        ]
    except Exception:
        files = []
    return jsonify({"files": files, "synced": synced_files})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"  YTDL server  → http://localhost:{port}")
    print(f"  LAN address  → http://{local_ip()}:{port}")
    print(f"  Download dir → {DOWNLOAD_DIR}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
