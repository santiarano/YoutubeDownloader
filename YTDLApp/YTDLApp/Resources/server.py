#!/usr/bin/env python3
"""
YoutubeDownloader backend — queue-based YouTube downloader.
H.264/AAC MP4, ≤640×400 (Meta Ray-Ban Display).
The iPhone app pulls finished files from this server over Wi-Fi (LAN).
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
STATE_FILE = os.path.expanduser("~/.youtubedownloader_state.json")
VIDEO_EXTS = (".mp4", ".m4v", ".mov")

# ── Persisted state ───────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"download_dir": os.path.expanduser("~/Downloads")}

def _save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"download_dir": DOWNLOAD_DIR}, f)
    except Exception:
        pass

DOWNLOAD_DIR = _load_state()["download_dir"]

# ── Queue ─────────────────────────────────────────────────────────────────────

queue       = []     # list of item dicts
queue_lock  = threading.Lock()
sse_clients = []     # list of queue.Queue for live updates

def _notify():
    snap = _snapshot()
    for q in list(sse_clients):
        try:
            q.put_nowait(snap)
        except Exception:
            pass

def _snapshot():
    with queue_lock:
        return json.dumps({"type": "queue", "items": queue, "download_dir": DOWNLOAD_DIR})

def _set(item_id, **kw):
    with queue_lock:
        for it in queue:
            if it["id"] == item_id:
                it.update(kw)
                break
    _notify()

def _add(urls):
    added = []
    with queue_lock:
        for url in urls:
            url = url.strip()
            if not url:
                continue
            it = {"id": str(uuid.uuid4())[:8], "url": url,
                  "status": "pending", "progress": 0, "filename": None, "error": None}
            queue.append(it)
            added.append(it)
    _notify()
    return added

# ── Worker (crash-proof) ──────────────────────────────────────────────────────

def _worker():
    while True:
        item = None
        with queue_lock:
            for it in queue:
                if it["status"] == "pending":
                    it["status"] = "downloading"
                    item = it
                    break
        if not item:
            time.sleep(0.5)
            continue

        try:
            _process(item)
        except Exception as e:
            # Never let one bad item kill the worker
            _set(item["id"], status="error", error=str(e))

def _process(item):
    _set(item["id"], status="downloading", progress=0, error=None)

    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore"

    # Prefer H.264 (avc1) ≤400p so we can remux (fast stream-copy) instead of
    # re-encoding. YouTube serves avc1 up to 360p (640×360) which fits the
    # 640×400 Ray-Ban Display panel perfectly.
    fmt = (
        "bestvideo[vcodec^=avc1][height<=400]+bestaudio[ext=m4a]"
        "/best[vcodec^=avc1][height<=400]"
        "/bestvideo[height<=400]+bestaudio/best[height<=400]/best"
    )

    cmd = [
        YTDLP, "--newline", "--no-playlist",
        "-f", fmt,
        "--remux-video", "mp4",
        "--postprocessor-args", "ffmpeg:-movflags +faststart",
        "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        item["url"],
    ]

    pct_re   = re.compile(r'\[download\]\s+([\d.]+)%')
    dest_re  = re.compile(r'\[(?:download|ExtractAudio)\] Destination:\s+(.+)')
    merge_re = re.compile(r'\[(?:Merger|VideoRemuxer)\].*?(?:into|to) "(.+)"')
    done_re  = re.compile(r'\[download\] (.+) has already been downloaded')

    last_file = None
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env)

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        m_pct, m_dest, m_merge, m_done = (pct_re.search(line), dest_re.search(line),
                                          merge_re.search(line), done_re.search(line))
        if m_dest or m_merge:
            last_file = (m_dest or m_merge).group(1).strip()
            _set(item["id"], filename=os.path.basename(last_file))
        elif m_done:
            last_file = m_done.group(1)
            _set(item["id"], filename=os.path.basename(last_file), progress=100)
        elif m_pct:
            _set(item["id"], progress=round(float(m_pct.group(1))))
        elif "[VideoRemuxer]" in line or "Remuxing" in line:
            _set(item["id"], progress=100)

    proc.wait()
    if proc.returncode == 0:
        # Resolve the final remuxed filename (extension may have changed to .mp4)
        name = os.path.basename(last_file) if last_file else item["filename"]
        if name:
            base = os.path.splitext(name)[0]
            mp4  = base + ".mp4"
            if os.path.exists(os.path.join(DOWNLOAD_DIR, mp4)):
                name = mp4
        _set(item["id"], status="done", progress=100, filename=name)
    else:
        _set(item["id"], status="error", error="yt-dlp exited with error")

threading.Thread(target=_worker, daemon=True).start()

# ── Helpers ───────────────────────────────────────────────────────────────────

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

# ── Routes: UI ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(SCRIPT_DIR, "index.html")

@app.route("/info")
def info():
    return jsonify({"ip": local_ip(), "port": int(os.environ.get("PORT", 5001))})

# ── Routes: queue ─────────────────────────────────────────────────────────────

@app.route("/queue", methods=["GET"])
def get_queue():
    return jsonify({"items": queue, "download_dir": DOWNLOAD_DIR})

@app.route("/queue/add", methods=["POST"])
def queue_add():
    raw  = request.get_json(force=True).get("urls", "")
    urls = [u for u in re.split(r"[\n,]+", raw) if u.strip()]
    return jsonify({"added": len(_add(urls))})

@app.route("/queue/<item_id>", methods=["DELETE"])
def queue_delete(item_id):
    with queue_lock:
        queue[:] = [i for i in queue if i["id"] != item_id or i["status"] == "downloading"]
    _notify()
    return jsonify({"ok": True})

@app.route("/queue/clear", methods=["POST"])
def queue_clear():
    with queue_lock:
        queue[:] = [i for i in queue if i["status"] == "downloading"]
    _notify()
    return jsonify({"ok": True})

@app.route("/queue/stream")
def queue_stream():
    import queue as Q
    client_q = Q.Queue()
    sse_clients.append(client_q)
    client_q.put_nowait(_snapshot())

    def generate():
        try:
            while True:
                try:
                    data = client_q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Q.Empty:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            try:
                sse_clients.remove(client_q)
            except ValueError:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

# ── Routes: folder ────────────────────────────────────────────────────────────

@app.route("/set-dir", methods=["POST"])
def set_dir():
    global DOWNLOAD_DIR
    path = request.get_json(force=True).get("path", "").strip()
    if not path or not os.path.isabs(path):
        return jsonify({"error": "Invalid path"}), 400
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    DOWNLOAD_DIR = path
    _save_state()
    _notify()
    return jsonify({"path": DOWNLOAD_DIR})

@app.route("/browse", methods=["POST"])
def browse():
    r = subprocess.run(
        ["osascript", "-e",
         'POSIX path of (choose folder with prompt "Select download folder:")'],
        capture_output=True, text=True)
    if r.returncode != 0:
        return jsonify({"cancelled": True})
    return jsonify({"path": r.stdout.strip()})

# ── Routes: file serving (iPhone pulls these over Wi-Fi) ──────────────────────

@app.route("/files")
def list_files():
    out = []
    try:
        for f in sorted(os.listdir(DOWNLOAD_DIR)):
            if f.lower().endswith(VIDEO_EXTS):
                p = os.path.join(DOWNLOAD_DIR, f)
                out.append({"name": f, "size": os.path.getsize(p)})
    except Exception:
        pass
    return jsonify({"files": out})

@app.route("/serve/<path:filename>")
def serve_video(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, conditional=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"  YoutubeDownloader server  → http://localhost:{port}")
    print(f"  LAN address  → http://{local_ip()}:{port}   (enter this in the iPhone app)")
    print(f"  Download dir → {DOWNLOAD_DIR}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
