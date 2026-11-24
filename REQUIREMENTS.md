# Hardware & Software Requirements

Hypermesh Media Hub is designed to run on hardware you **already own** — including an old PC, spare laptop, Raspberry Pi, or even an unused Android phone.

---

## 1. Hardware Requirements by Device Type

### Option A: Old PC or Spare Laptop (Windows / Linux / macOS)
Great for repurposing an older 2012+ desktop or laptop as a home media server.

| Component | Minimum | Recommended |
| :--- | :--- | :--- |
| **CPU** | Any 64-bit dual-core CPU (e.g., Intel Core i3 / Celeron / AMD Athlon) | Quad-core CPU for faster video thumbnailing & concurrent streams |
| **RAM** | **1 GB** (Linux headless) / **2 GB** (Windows) | **4 GB+** |
| **Storage** | **2 GB** free for app + Python dependencies + cache | **50 GB – 4 TB+** (internal HDD/SSD or external USB drive) |
| **Network** | Wi-Fi (802.11n/ac) or 100 Mbps Ethernet | Gigabit Ethernet or 5 GHz Wi-Fi for smooth 1080p/4K streaming |

> **Tip for Old Laptops:** Keep the lid open or configure OS power settings to *"Do nothing when lid is closed"* so the server stays awake 24/7 while plugged in.

---

### Option B: Raspberry Pi (Silent 24/7 Low-Power Server)
Ideal for an always-on low-electricity home hub (~3–8W).

| Board | Support Status | Notes |
| :--- | :--- | :--- |
| **Raspberry Pi 5 / Pi 4 (2GB–8GB)** | **Recommended** | Smooth multi-device 1080p/4K streaming, fast `yt-dlp` + FFmpeg audio conversion |
| **Raspberry Pi 3B+ / Zero 2 W** | Supported (1GB / 512MB RAM) | Best for music streaming and single 720p/1080p direct-play video; add a 1–2 GB swap file |
| **OS** | **Raspberry Pi OS (64-bit / `aarch64`)** or Ubuntu Server ARM64 | `tunnel.py` automatically fetches `cloudflared-linux-arm64` on ARM64 |
| **Storage** | 16 GB+ microSD (OS) + **External USB SSD/HDD** (media) | Store large media folders on an external USB drive to preserve microSD lifespan |

---

### Option C: Mobile Phone / Tablet as the Server (Android via Termux)
Have an old Android phone with a cracked screen or a spare tablet? You can run the Media Hub Python server **directly on the phone** and stream to other phones, laptops, or TVs over Wi-Fi/Hotspot!

| Requirement | Minimum | Recommended |
| :--- | :--- | :--- |
| **Android Version** | Android 8.0+ | Android 10+ (64-bit ARM64) |
| **RAM** | **2 GB** | **4 GB+** |
| **Storage** | **2 GB** free internal space + media storage | **32 GB+** (or SD card) |
| **App** | **Termux** (install from F-Droid or GitHub Releases, **not** the outdated Play Store version) | Keep battery optimization disabled for Termux |

---

### Option D: Client Devices (Playback Only)
Any device on your Wi-Fi, Tailscale network, or internet (via Cloudflare Tunnel) can stream from the Hub:
- **Android / iOS App:** [`sahaveer/hypermesh-mobile-app`](https://github.com/sahaveer/hypermesh-mobile-app)
- **Web Browser (PWA):** Chrome, Edge, Safari, Firefox on Windows, macOS, Linux, Android, or iOS
- **Smart TVs / Media Boxes:** Any DLNA/UPnP client, Kodi, or VLC Media Player

---

## 2. Software Requirements (Server Host)

### Core Python Environment
- **Python 3.10 or newer** (tested on Python 3.10 – 3.13)
- Python packages (installed via `pip install -r requirements.txt`):
  - `fastapi>=0.110.0` & `uvicorn[standard]>=0.28.0` — HTTP/206 streaming API & ASGI server
  - `mutagen>=1.47.0` — Audio ID3/MP4/FLAC metadata & cover art extraction
  - `pillow>=10.0.0` — Photo & cover thumbnail optimization
  - `pydantic>=2.0.0` & `python-multipart>=0.0.9` — Request validation & file uploads
  - `yt-dlp[default]>=2026.8.19` — Universal media downloader engine
  - `zeroconf>=0.132.0` — Local Wi-Fi mDNS peer discovery
  - `qrcode>=7.4.2` — Terminal & web pairing QR codes
  - `imageio-ffmpeg>=0.4.9` — Fallback bundled FFmpeg binary

### Required for YouTube & Video Downloading (`yt-dlp`)
1. **JavaScript Runtime (Required for YouTube):**
   - **Node.js 22+** (`node`) **OR** **Deno 2.3+** (`deno`) must be installed and available on `PATH` (or standard system paths checked by `downloader.py`).
2. **FFmpeg:**
   - System `ffmpeg` installed on `PATH` (recommended on Linux, Raspberry Pi, macOS, Termux), or the bundled `imageio-ffmpeg` fallback on x86_64 Windows/Linux.
   - Used for extracting MP3/M4A audio, merging high-resolution video + audio streams, and generating video preview snapshots.

### Optional Networking Tools (For Remote Access Outside Home Wi-Fi)
- **Tailscale** (recommended for private encrypted P2P access): Free personal account, gives your server a permanent `100.x.y.z` IP across all your devices with zero port forwarding.
- **Cloudflare Tunnel (`cloudflared`)**: Automatically downloaded on demand by `tunnel.py` / `run_server.py` (or install system-wide) for instant `https://*.trycloudflare.com` access from any browser or mobile network.
- **PM2** (Node.js process manager) or **systemd** (built into Linux/Raspberry Pi OS) for running 24/7 on boot.
