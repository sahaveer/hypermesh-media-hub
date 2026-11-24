# Step-by-Step Setup Guide (PC, Old Laptop, Raspberry Pi & Mobile)

Follow the section below that matches the device you want to use as your **Hypermesh Media Hub server**.

---

## 1. Windows (PC or Old Laptop)

### Step 1: Install Prerequisites
1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/) — check **"Add Python to PATH"** during installation.
2. Install **Node.js 22+ LTS** from [nodejs.org](https://nodejs.org/) (needed for YouTube downloads via `yt-dlp`).
3. *(Optional)* Install **FFmpeg** (`winget install Gyan.FFmpeg`) or rely on `imageio-ffmpeg` from `requirements.txt`.
4. Install **Git** from [git-scm.com](https://git-scm.com/).

### Step 2: Clone & Start
```powershell
git clone https://github.com/sahaveer/hypermesh-media-hub.git
cd hypermesh-media-hub
```
Double-click **`start_player.bat`** (local Wi-Fi mesh) or **`start_global_player.bat`** (local + Cloudflare worldwide HTTPS tunnel).  
The script automatically creates a `venv`, installs `requirements.txt`, starts the server on port `8000`, and opens `http://localhost:8000`.

### Old Windows Laptop Tip
Open **Control Panel → Power Options → Choose what closing the lid does** and set **"When I close the lid"** (Plugged in) to **Do nothing**, then turn off automatic sleep so your server stays reachable.

---

## 2. Linux / Ubuntu / Old Laptop (24/7 Server)

### Step 1: Install System Packages
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git curl
```
Install **Node.js 22+** (required by `yt-dlp` for YouTube):
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

### Step 2: Clone & Install Python Dependencies
```bash
git clone https://github.com/sahaveer/hypermesh-media-hub.git
cd hypermesh-media-hub
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Run Manually
```bash
# Local Wi-Fi + Cloudflare Tunnel:
python3 run_server.py

# Local Wi-Fi only (no Cloudflare Tunnel):
python3 run_server.py --local-only
```

### Step 4: Linux 24/7 Service
#### Option A: `systemd`
```bash
sudo mkdir -p /opt/mediaplayer
sudo cp -r . /opt/mediaplayer/
sudo cp mediaplayer.service /etc/systemd/system/mediaplayer@.service
sudo systemctl daemon-reload
sudo systemctl enable --now mediaplayer@$USER
```
*(Or edit `mediaplayer.service` to match your install path and username.)*

#### Option B: `pm2`
```bash
sudo npm install -g pm2
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

---

## 3. Raspberry Pi (Pi 3B+, 4, 5, Zero 2 W)

1. Flash **Raspberry Pi OS Lite (64-bit)** or **Raspberry Pi OS Desktop (64-bit)** using Raspberry Pi Imager.
2. SSH into your Pi or open a terminal and install prerequisites:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg git curl
   curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
   sudo apt install -y nodejs
   ```
3. Clone and set up the hub:
   ```bash
   git clone https://github.com/sahaveer/hypermesh-media-hub.git
   cd hypermesh-media-hub
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Mount your external USB HDD/SSD** (e.g., `/mnt/media`) and add it in the Web UI under **Privacy & Folders** (or **Settings**) so your movies/music stay off the microSD card.
5. *(Pi Zero 2 W / 1 GB RAM models)* Add a 1–2 GB swap file if running heavy downloads.
6. Run `python3 run_server.py` (or configure `systemd` / `pm2` above to start on boot).

---

## 4. Android Phone via Termux (Pocket Server)

You can turn an old or spare Android phone into a portable media hub:

1. Install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) (recommended) and grant Storage permission:
   ```bash
   termux-setup-storage
   ```
2. Update packages and install Python, Node.js, FFmpeg, and Git:
   ```bash
   pkg update && pkg upgrade -y
   pkg install -y python nodejs-lts ffmpeg git
   ```
3. Clone and install:
   ```bash
   git clone https://github.com/sahaveer/hypermesh-media-hub.git
   cd hypermesh-media-hub
   pip install -r requirements.txt
   ```
4. Start the hub in local mesh mode:
   ```bash
   python run_server.py --local-only
   ```
5. Disable Android battery optimization for Termux and acquire a Termux wake-lock (`termux-wake-lock`) so Android does not sleep the server while streaming.
6. Open the Web UI at `http://localhost:8000` on that phone, add your `/sdcard/Music` or `/sdcard/Movies` folder, and connect from other phones or laptops on the same Wi-Fi or mobile hotspot!

---

## 5. Connecting From Anywhere (Tailscale & Cloudflare)

### Tailscale (Private Encrypted Mesh — Recommended)
1. Install [Tailscale](https://tailscale.com/download) on your server (PC / Laptop / Raspberry Pi) and on your phone.
2. Sign in with the same account on both devices.
3. Your server receives a permanent private IP like `100.x.y.z`.
4. Open `http://100.x.y.z:8000` in your browser or enter it in the **Hypermesh Mobile App** (`sahaveer/hypermesh-mobile-app`).

### Cloudflare Quick Tunnel (Public HTTPS Without Port Forwarding)
- Start the server with `python run_server.py` (default) or toggle **Worldwide Access** inside the Web UI.
- Scan the generated **Worldwide QR** code on any phone over 4G/5G.

---

## 6. Configuring YouTube & APK Sync

- **YouTube Downloads (`yt-dlp`):** Requires **Node.js 22+** or **Deno 2.3+** + **FFmpeg**. If you upgrade `yt-dlp` later, run:
  ```bash
  pip install -U "yt-dlp[default]"
  ```
- **Mobile App APK Sync:** By default, `/api/app/sync-github` pulls releases from `sahaveer/hypermesh-mobile-app`. Copy `.env.example` to `.env` if you want to customize `PORT`, `HOST`, or supply an optional `APK_GITHUB_TOKEN`.
