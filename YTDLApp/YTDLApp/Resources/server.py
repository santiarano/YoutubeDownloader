#!/usr/bin/env python3
"""
YTDL Flask backend — streams yt-dlp progress as NDJSON to the frontend.
Also serves downloaded videos so iOS devices on the same network can stream them.
Run: python3 server.py
"""
import json
import os
import re
import socket
import subprocess
from flask import Flask, request, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
YTDLP      = os.path.expanduser("~/Library/Python/3.13/bin/yt-dlp")

# All downloaded videos land here and are served over HTTP so iOS can stream them
SERVE_DIR  = os.path.expanduser("~/ytdl/served")
os.makedirs(SERVE_DIR, exist_ok=True)


def local_ip() -> str:
    """Best-effort local LAN IP (en0 / Wi-Fi)."""
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
    """Stream a previously-downloaded video to any device on the LAN."""
    return send_from_directory(SERVE_DIR, filename, conditional=True)


@app.route("/download", methods=["POST"])
def download():
    data   = request.get_json(force=True)
    url    = data.get("url", "").strip()
    fmt    = data.get("format", "best")   # best | mp4 | mp3
    # "liftlens" mode: always saves to SERVE_DIR as MP4 for iOS streaming
    mode   = data.get("mode", "default")  # "default" | "liftlens"

    if mode == "liftlens":
        out_dir = SERVE_DIR
        fmt = "mp4"
    else:
        out_dir = data.get("dir", None) or os.path.expanduser("~/Downloads")
        out_dir = os.path.expanduser(out_dir)

    os.makedirs(out_dir, exist_ok=True)

    if not url:
        return Response(
            json.dumps({"type": "error", "message": "No URL provided"}) + "\n",
            mimetype="application/x-ndjson"
        )

    cmd = [YTDLP, "--newline", "--no-playlist"]

    if fmt == "mp3":
        cmd += ["-x", "--audio-format", "mp3"]
    elif fmt == "mp4":
        cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best"]

    out_tmpl = os.path.join(out_dir, "%(title)s.%(ext)s")
    cmd += ["-o", out_tmpl, url]

    port = int(os.environ.get("PORT", 5001))
    ip   = local_ip()

    def generate():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        pct_re   = re.compile(r'\[download\]\s+([\d.]+)%')
        dest_re  = re.compile(r'\[(?:download|ExtractAudio)\] Destination:\s+(.+)')
        merge_re = re.compile(r'\[Merger\] Merging formats into "(.+)"')
        done_re  = re.compile(r'\[download\] (.+) has already been downloaded')

        last_file = None

        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue

            pct_match   = pct_re.search(line)
            dest_match  = dest_re.search(line)
            merge_match = merge_re.search(line)
            done_match  = done_re.search(line)

            if dest_match or merge_match:
                last_file = (dest_match or merge_match).group(1).strip()
                msg = {"type": "progress", "pct": None, "status": "Downloading...", "text": line}
            elif pct_match:
                pct = round(float(pct_match.group(1)))
                msg = {"type": "progress", "pct": pct, "status": f"Downloading {pct}%", "text": line}
            elif done_match:
                last_file = done_match.group(1)
                msg = {"type": "progress", "pct": 99, "status": "Already downloaded", "text": line}
            elif "[ExtractAudio]" in line:
                msg = {"type": "progress", "pct": None, "status": "Converting to MP3...", "text": line}
            elif "[Merger]" in line:
                msg = {"type": "progress", "pct": None, "status": "Merging streams...", "text": line}
            else:
                msg = {"type": "progress", "pct": None, "status": "Processing...", "text": line}

            yield json.dumps(msg) + "\n"

        proc.wait()
        if proc.returncode == 0:
            serve_url = None
            if last_file and mode == "liftlens":
                filename = os.path.basename(last_file)
                serve_url = f"http://{ip}:{port}/serve/{filename}"
            yield json.dumps({"type": "done", "file": last_file, "serve_url": serve_url}) + "\n"
        else:
            yield json.dumps({"type": "error", "message": "yt-dlp exited with error"}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"  YTDL server  → http://localhost:{port}")
    print(f"  LAN address  → http://{local_ip()}:{port}")
    print(f"  Served dir   → {SERVE_DIR}")
    app.run(host="0.0.0.0", port=port, debug=False)
