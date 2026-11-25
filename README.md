# Hypermesh Media Hub (Open-Source Personal Media Server, Mesh Hub & `yt-dlp` Downloader)

[![Download Android App APK](https://img.shields.io/badge/Android_App_APK-v1.0.0%2B1-00E5FF?style=for-the-badge&logo=android&logoColor=black)](https://github.com/sahaveer/hypermesh-mobile-app/releases/latest/download/hypermeshmediaplayer.apk)

An open-source, self-hosted **Peer-to-Peer Mesh Media Server & Universal Downloader** built with **FastAPI**, **yt-dlp**, **FFmpeg**, and a built-in **Progressive Web App (PWA)** player.

- **Mesh Media Sharing:** Share your local **audio, movies, videos, and photos** across anyone connected on your Wi-Fi or Tailscale network with **0 MB internet data used**.
- **Personal Offline Server + YouTube (`yt-dlp`) Capability:** Turn **any old PC, spare laptop, Raspberry Pi, Linux mini-server, or spare Android phone (Termux)** into your own offline music/video server with built-in YouTube & 1,000+ site downloading.

---

## Download the Android App Directly

Don't want to compile the mobile app yourself? Download and install it directly on any Android phone:

- **Direct APK Download:** [`hypermeshmediaplayer.apk` (Latest Release)](https://github.com/sahaveer/hypermesh-mobile-app/releases/latest/download/hypermeshmediaplayer.apk)
- **GitHub Release Page:** https://github.com/sahaveer/hypermesh-mobile-app/releases/tag/v1.0.0%2B1
- **Mobile App Source Repo:** https://github.com/sahaveer/hypermesh-mobile-app

> Even without a PC server, phones running the Android app on the same Wi-Fi/hotspot can share audio and video with each other over the local mesh. Adding **this server repo** on a PC/laptop/Raspberry Pi unlocks always-on offline libraries and server-side **`yt-dlp` YouTube downloading**.

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
|  2. MOBILE CLIENT: sahaveer/hypermesh-mobile-app (Android APK)    |
|     Runs on: Android / iOS / Web / Desktop                        |
|     - Phone-to-phone mesh sharing across anyone on your network   |
|     - Connects to your Hub over Wi-Fi, Tailscale, or Cloudflare   |
|     - Aggregates multiple Hubs into one unified Hypermesh pool    |
|     - Streams & pins offline copies + sends yt-dlp jobs to Hub    |
+-------------------------------------------------------------------+
```

---

## Where Can You Run This Server?

You do **not** need expensive hardware. Hypermesh Media Hub runs almost anywhere:

| Host Device | OS / Environment | Ideal For | Guide |
| :--- | :--- | :--- | :--- |
| **Old PC or Spare Laptop** | Windows 10/11, Ubuntu, Linux Mint, macOS | Home media server with large HDD/SSD | [REQUIREMENTS.md](REQUIREMENTS.md) & [SETUP_GUIDE.md](SETUP_GUIDE.md) |
| **Raspberry Pi** (Pi 3B+, 4, 5, Zero 2 W) | Raspberry Pi OS (64-bit recommended) | Silent 24/7 low-power media box | [SETUP_GUIDE.md#3-raspberry-pi-pi-3b-4-5-zero-2-w](SETUP_GUIDE.md#3-raspberry-pi-pi-3b-4-5-zero-2-w) |
| **Spare Android Phone / Tablet** | Android + **Termux** | Portable pocket/travel media hub | [SETUP_GUIDE.md#4-android-phone-via-termux-pocket-server](SETUP_GUIDE.md#4-android-phone-via-termux-pocket-server) |
| **Always-On Linux VPS / Mini-PC** | Debian / Ubuntu (`systemd` or `pm2`) | 24/7 personal cloud via Tailscale | [SETUP_GUIDE.md#2-linux--ubuntu--old-laptop-247-server](SETUP_GUIDE.md#2-linux--ubuntu--old-laptop-247-server) |

See [REQUIREMENTS.md](REQUIREMENTS.md) for exact CPU, RAM, storage, Python, Node.js/Deno, and FFmpeg requirements.

---

## Quick Start: Create Your Own Server, Connect Local Music & Download YouTube

### 1. Check Prerequisites
- **Python 3.10+**
- **Node.js 22+** or **Deno 2.3+** (needed by `yt-dlp` for YouTube downloads)
- **FFmpeg** (for audio extraction, video merging, and thumbnails)

### 2. Clone & Start on Your Computer
```bash
git clone https://github.com/sahaveer/hypermesh-media-hub.git
cd hypermesh-media-hub

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS / Raspberry Pi:
source venv/bin/activate

pip install -r requirements.txt
python run_server.py
```
- **Windows 1-Click Launchers:** Double-click `start_player.bat` (local Wi-Fi mesh) or `start_global_player.bat` (mesh + worldwide Cloudflare tunnel).
- **Offline Wi-Fi Only (No Tunnel):** `python run_server.py --local-only`

### 3. Connect Your Local Music & Video Folders
1. Open `http://localhost:8000` in your browser.
2. Go to **Privacy & Folders** and add the folders where your offline Music, Movies, Videos, or Photos live.
3. Anyone on your network (via browser, Smart TV DLNA, VLC, or the [Android App](https://github.com/sahaveer/hypermesh-mobile-app/releases/latest/download/hypermeshmediaplayer.apk)) can now browse and stream them offline.

### 4. Connect the Android App & Download YouTube Songs/Videos Later On
1. Install the APK on your phone from [`hypermeshmediaplayer.apk`](https://github.com/sahaveer/hypermesh-mobile-app/releases/latest/download/hypermeshmediaplayer.apk).
2. In the app's **Hub Settings**, enter your server URL (shown in the server terminal or QR code: Wi-Fi `http://192.168.x.x:8000`, Tailscale `http://100.x.y.z:8000`, or Cloudflare `https://*.trycloudflare.com`).
3. Use the **Downloader** in either the Web UI or the Android app to paste YouTube/media links or song names and save them as **MP3/M4A** or **MP4** directly into your server library (or straight to your phone).

---

## Key Features

- **Multi-Mode Connectivity**: Local Wi-Fi Mesh (`0 MB` internet data, mDNS/Zeroconf discovery), Tailscale Private Mesh (`100.x.y.z`), and Cloudflare Tunnel (`https://*.trycloudflare.com`).
- **Universal Downloader (`yt-dlp` + FFmpeg)**: Download audio or video from YouTube, SoundCloud, Vimeo, and 1,000+ sites, plus Spotify metadata/search resolution.
- **Smart Library & Photo Gallery**: Automatic scanning of Music, Videos/Movies (with TV episode parsing and FFmpeg thumbnails), and Photos, plus Stealth mode and upload approval queue.
- **DLNA / UPnP, VLC & Smart TVs**: Built-in SSDP/DLNA server on UDP `1900`, `vlc://` deep links, and dynamic M3U playlists (`/api/playlist.m3u`, `/api/playlist/movies.m3u`, `/api/playlist/music.m3u`).
- **Built-in Mobile App Sync**: Syncs the latest Android APK from [`sahaveer/hypermesh-mobile-app`](https://github.com/sahaveer/hypermesh-mobile-app) and serves it to local devices at `/hypermeshmediaplayer.apk`.

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
