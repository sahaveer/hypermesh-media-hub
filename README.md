# Hypermesh Media Hub (Open-Source Personal Media Server & Web PWA)

An open-source, self-hosted, peer-to-peer **Media Streaming, Sharing & Universal Downloader Hub** built with **FastAPI**, **yt-dlp**, **FFmpeg**, and a built-in **Progressive Web App (PWA)** player.

Turn **any old PC, spare laptop, Raspberry Pi, Linux mini-server, or spare Android phone (Termux)** into your own personal Spotify + Plex + media downloader cloud with zero subscription fees.

> **Companion Mobile App:** Pair this server with the native Android / Flutter client in [`sahaveer/hypermesh-mobile-app`](https://github.com/sahaveer/hypermesh-mobile-app) — or stream directly from any browser, Smart TV (DLNA), or VLC!

---

## Architecture Overview

```
+-------------------------------------------------------------------+
|                   HYPERMESH ECOSYSTEM (2 REPOS)                   |
+-------------------------------------------------------------------+
|                                                                   |
|  1. THIS REPO: hypermesh-media-hub (Server + Web PWA + yt-dlp)    |
|     Runs on: Old PC / Laptop / Raspberry Pi / Linux / Termux      |
|     - FastAPI media API + SQLite library index                    |
|     - HTTP 206 byte-range audio & video streaming                 |
|     - Universal downloader (yt-dlp + FFmpeg + Node.js/Deno)       |
|     - mDNS/Zeroconf Wi-Fi mesh + UPnP/DLNA Smart TV discovery     |
|     - Tailscale P2P & Cloudflare Tunnel remote HTTPS access       |
|     - Built-in Web PWA (/), TV UI (/tv), and APK OTA endpoint     |
|                                                                   |
|  2. MOBILE CLIENT: sahaveer/hypermesh-mobile-app (Flutter App)    |
|     Runs on: Android / iOS / Web / Desktop                        |
|     - Connects to your Hub over Wi-Fi, Tailscale, or Cloudflare   |
|     - Aggregates multiple Hubs into one unified Hypermesh pool    |
|     - Streams & pins offline copies + sends downloads to your Hub |
+-------------------------------------------------------------------+
```

---

## Where Can You Run This Server?

You do **not** need expensive hardware. Hypermesh Media Hub is lightweight and runs almost anywhere:

| Host Device | OS / Environment | Ideal For | Guide |
| :--- | :--- | :--- | :--- |
| **Old PC or Spare Laptop** | Windows 10/11, Ubuntu, Linux Mint, macOS | Home media server with large HDD/SSD | [REQUIREMENTS.md](REQUIREMENTS.md) & [SETUP_GUIDE.md](SETUP_GUIDE.md) |
| **Raspberry Pi** (Pi 3B+, 4, 5, Zero 2 W) | Raspberry Pi OS (64-bit recommended) | Silent 24/7 low-power media box | [SETUP_GUIDE.md#raspberry-pi](SETUP_GUIDE.md#raspberry-pi) |
| **Spare Android Phone / Tablet** | Android + **Termux** | Portable pocket/travel media hub | [SETUP_GUIDE.md#android-phone-via-termux](SETUP_GUIDE.md#android-phone-via-termux) |
| **Always-On Linux VPS / Mini-PC** | Debian / Ubuntu (`systemd` or `pm2`) | 24/7 personal cloud via Tailscale | [SETUP_GUIDE.md#linux-247-service](SETUP_GUIDE.md#linux-247-service) |

See [REQUIREMENTS.md](REQUIREMENTS.md) for exact CPU, RAM, storage, Python, Node.js/Deno, and FFmpeg requirements.

---

## Key Features

- **Multi-Mode Connectivity**:
  - **Local Wi-Fi Mesh**: 100% offline streaming over LAN/hotspot with `0 MB` internet data used and automatic mDNS/Zeroconf discovery.
  - **Tailscale Private Mesh**: Direct encrypted WireGuard access (`100.x.y.z`) from anywhere with zero port forwarding.
  - **Worldwide Global Access**: Optional Cloudflare Tunnel (`cloudflared`) HTTPS link for 4G/5G access and QR pairing.
- **Universal Downloader (`yt-dlp` + FFmpeg)**:
  - Download audio (MP3/M4A) or video (MP4) from YouTube, SoundCloud, Vimeo, and 1,000+ `yt-dlp`-supported sites, plus Spotify metadata/search resolution.
  - Save directly to the **Server Library** (shared across devices) or stage for **My Device** download.
- **Smart Media Library & Photo Gallery**:
  - Automatic scanning of Music, Videos/Movies (with TV episode parsing and FFmpeg thumbnails), and Photos (timeline view).
  - Privacy toggle ("Share My Library" / Stealth mode), folder opt-in, and optional host approval queue for peer uploads.
- **DLNA / UPnP, VLC & Smart TVs**:
  - Built-in SSDP/DLNA server on UDP `1900`, `vlc://` deep links, and dynamic M3U playlists (`/api/playlist.m3u`, `/api/playlist/movies.m3u`, `/api/playlist/music.m3u`).
- **Built-in Mobile App Sync**:
  - Sync the latest Android APK directly from [`sahaveer/hypermesh-mobile-app`](https://github.com/sahaveer/hypermesh-mobile-app) releases and serve it to devices on your network via `/hypermeshmediaplayer.apk`.

---

## Quick Start (Any PC or Laptop)

### 1. Check Prerequisites
Make sure you have:
- **Python 3.10+**
- **Node.js 22+** or **Deno 2.3+** (needed by `yt-dlp` for YouTube downloads)
- **FFmpeg** (optional if using bundled `imageio-ffmpeg`, recommended for best performance)

Full details: [REQUIREMENTS.md](REQUIREMENTS.md)

### 2. Clone & Install
```bash
git clone https://github.com/sahaveer/hypermesh-media-hub.git
cd hypermesh-media-hub

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS / Raspberry Pi:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Run the Server
- **Local Wi-Fi Mesh + Global Tunnel (default):**
  ```bash
  python run_server.py
  ```
- **Local Wi-Fi Mesh Only (100% offline, no Cloudflare tunnel):**
  ```bash
  python run_server.py --local-only
  ```
- **Windows 1-Click Launchers:**
  - `start_player.bat` (local mesh)
  - `start_global_player.bat` (mesh + worldwide Cloudflare tunnel)

Open `http://localhost:8000` in your browser, or scan the terminal QR code from your phone or the [Hypermesh Mobile App](https://github.com/sahaveer/hypermesh-mobile-app).

---

## Documentation

- [REQUIREMENTS.md](REQUIREMENTS.md) — Hardware & software requirements for Old PCs, Laptops, Raspberry Pi, and Mobile (Termux)
- [SETUP_GUIDE.md](SETUP_GUIDE.md) — Complete step-by-step setup for Windows, Linux, Raspberry Pi, Android Termux, Tailscale, and `yt-dlp`
- [CONTRIBUTING.md](CONTRIBUTING.md) — Project architecture, running unit tests, and how to contribute

---

## Running Tests

```bash
python -m unittest discover -s . -p "test_*.py"
```

---

## License

Released under the [MIT License](LICENSE). Everyone is welcome to fork, run, customize, and build on top of Hypermesh Media Hub.
