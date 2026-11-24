# Contributing to Hypermesh Media Hub

Thank you for helping make open-source, self-hosted, peer-to-peer media streaming accessible to everyone!

---

## Repository Structure

```
hypermesh-media-hub/
├── main.py                  # FastAPI application, REST endpoints, Web/TV routes, APK sync
├── run_server.py            # CLI launcher (local Wi-Fi, Tailscale IP detection, QR, Cloudflare)
├── config.py                # Paths, supported media extensions, .env loader, HOST/PORT
├── settings.py              # Persistent JSON settings (shared folders, privacy, admin rules)
├── database.py              # SQLite schema & queries (tracks, videos, photos, playlists, statuses)
├── scanner.py               # Media scanner, FFmpeg video snapshots, EXIF/ID3 metadata parsing
├── streamer.py              # HTTP 206 byte-range audio/video streaming helpers
├── downloader.py            # yt-dlp + FFmpeg download queue (YouTube, Spotify search, 1000+ sites)
├── mesh.py                  # Zeroconf/mDNS local Wi-Fi peer discovery
├── dlna.py                  # UPnP / DLNA SSDP discovery & ContentDirectory XML
├── tunnel.py                # Cloudflare Tunnel (cloudflared) lifecycle manager
├── static/                  # Built-in Web PWA (index.html, app.js, style.css, sw.js) & TV UI (tv.html)
├── start_player.bat         # Windows 1-click local mesh launcher
├── start_global_player.bat  # Windows 1-click local + Cloudflare launcher
├── ecosystem.config.js      # PM2 process config
├── mediaplayer.service      # systemd service template
└── test_*.py                # Unit & integration tests
```

---

## Development Setup

```bash
git clone https://github.com/sahaveer/hypermesh-media-hub.git
cd hypermesh-media-hub
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Run the server with auto-reload during development:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Running Tests

Run the full test suite before opening a Pull Request:
```bash
python -m unittest discover -s . -p "test_*.py"
```

---

## Guidelines

1. **Privacy & No Secrets:** Never commit `.env`, `data/settings.json`, `data/*.db`, or personal IP addresses/tokens.
2. **Cross-Platform Compatibility:** Keep changes compatible with Windows, Linux (x86_64 & Raspberry Pi ARM64), macOS, and Android Termux.
3. **Open-Source Standards:** Keep dependencies minimal and well-documented in `requirements.txt` and `REQUIREMENTS.md`.
4. **Tests:** Add or update `test_*.py` tests for any new API endpoint or backend behavior.
