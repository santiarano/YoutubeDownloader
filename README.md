# YoutubeDownloader

A queue-based YouTube downloader tuned for the **Meta Ray-Ban Display** glasses, with a polished dark web UI, a Flask backend, a standalone macOS app wrapper, and **wireless sync** to the companion LiftLens iPhone app.

Built around [yt-dlp](https://github.com/yt-dlp/yt-dlp) + ffmpeg.

---

## What it does

- **Queue downloads** — paste one or many YouTube URLs, they download one after another with live progress.
- **Right format, automatically** — every video is H.264 (avc1) ≤640×400, AAC audio, MP4 with faststart. No format picker — one target, always correct for the glasses' 640×400 panel.
- **Fast** — prefers YouTube's H.264 streams and *remuxes* (stream-copy) into MP4 instead of re-encoding, so downloads finish in seconds.
- **Browse for a download folder** — native macOS folder picker; the choice persists.
- **Wireless sync to iPhone** — the LiftLens app pulls finished videos from the Mac over Wi-Fi, downloading only what it doesn't already have.

---

## Structure

```
YoutubeDownloader/
├── server.py          # Flask backend — queue, yt-dlp worker, file server
├── index.html         # Web UI — queue, folder picker, LAN address for phone
└── YTDLApp/           # Standalone macOS app (SwiftUI wrapper around the server)
    └── YTDLApp/
        ├── YTDLAppApp.swift      # Entry point, starts server on launch
        ├── ContentView.swift     # WKWebView loading localhost:5001
        ├── ServerManager.swift   # Finds Python+Flask, launches server.py
        └── Resources/            # Bundled server.py + index.html
```

---

## Usage

### Option A — macOS App (no terminal)

1. Open `YTDLApp/YTDLApp.xcodeproj` in Xcode → **⌘R**.
2. The app finds a Python 3 with Flask, starts the server, and shows the UI.

### Option B — Web (terminal)

```bash
pip install flask flask-cors yt-dlp
cd YoutubeDownloader
python3 server.py        # → http://localhost:5001
```

---

## Wireless sync to iPhone (LiftLens)

The Mac is the source of truth; the **iPhone pulls** from it over the LAN — no USB, no cables.

**Flow:**
1. Run the Mac server. It prints (and the sidebar shows) its LAN address, e.g. `http://192.168.1.50:5001`.
2. In the LiftLens iPhone app → **Sync from Mac**, enter that address and tap **Sync from Mac**.
3. The app fetches the Mac's file list, compares it against its own library (by name + size), and downloads only the new files into `Documents/MetaDisplayVideos`.
4. New videos appear in the list, ready to stream to the glasses.

**Why `Documents/MetaDisplayVideos`?** It's the app's own sandboxed folder — owned by the app, persists across launches, included in backups, and the in-app video server streams straight from it with no security-scoped-access dance.

**Mac endpoints the phone uses:**
| Endpoint | Purpose |
|----------|---------|
| `GET /files` | `{ "files": [ { "name", "size" } ] }` — the library manifest |
| `GET /serve/<filename>` | video bytes (HTTP range supported) |
| `GET /info` | `{ "ip", "port" }` — LAN address shown in the UI |

iOS side: `DisplayAccess/ViewModels/MacSyncService.swift` + the *Sync from Mac* section in `SampleAppsView.swift`.

---

## Backend API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/queue` | GET | current queue + download dir |
| `/queue/add` | POST | `{ "urls": "url1\nurl2" }` → enqueue |
| `/queue/<id>` | DELETE | remove a queued/finished item |
| `/queue/clear` | POST | clear all finished items |
| `/queue/stream` | GET | Server-Sent Events — live queue updates |
| `/set-dir` | POST | `{ "path": "/abs/path" }` |
| `/browse` | POST | open native folder picker |
| `/files` | GET | list downloaded videos (name + size) |
| `/serve/<file>` | GET | stream a downloaded video |
| `/info` | GET | LAN ip + port |

---

## Target format

| | |
|--|--|
| Codec | H.264 (avc1), source profile |
| Resolution | ≤640×400 (YouTube serves 640×360 at this cap) |
| Container | MP4 + faststart |
| Audio | AAC |
| Method | remux / stream-copy (re-encode only if unavoidable) |

---

## Requirements

| Dependency | Install |
|------------|---------|
| Python 3.10+ | `brew install python` or [python.org](https://python.org) |
| yt-dlp | `pip install yt-dlp` |
| Flask + flask-cors | `pip install flask flask-cors` |
| ffmpeg | `brew install ffmpeg` (remux + faststart) |
| Xcode 15+ | to build the macOS app |

---

## Notes

- Flask's dev server is fine for local use — don't expose port 5001 to the public internet.
- The macOS app copies `server.py`/`index.html` to a temp dir at launch, so rebuild in Xcode to pick up source edits.
- Phone and Mac must be on the same Wi-Fi for sync.
