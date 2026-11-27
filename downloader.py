import os
import re
import glob
import time
import uuid
import shutil
import threading
from threading import Thread
import logging
import pathlib
import json
import urllib.request
import urllib.parse
import sys
import subprocess
import signal
import importlib.util
from typing import Dict, Any, Optional, Tuple, List, Set
import yt_dlp

# Detect FFmpeg: prioritize system ffmpeg (e.g. Linux /usr/bin/ffmpeg or Windows PATH)
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        FFMPEG_PATH = None

# Detect Node.js: essential for solving modern YouTube JS challenges (n-sig, bot-check)
NODE_PATH = shutil.which("node")
if not NODE_PATH:
    common_node_paths = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.expanduser(r"~\AppData\Roaming\nvm\default\node.exe"),
        "/usr/bin/node",
        "/usr/local/bin/node",
        "/data/data/com.termux/files/usr/bin/node",
    ]
    for np in common_node_paths:
        if os.path.exists(np):
            NODE_PATH = np
            break

import config
import settings
import scanner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("downloader")

_download_tasks: Dict[str, Dict[str, Any]] = {}
_active_processes: Dict[str, subprocess.Popen] = {}
_worker_done_events: Dict[str, threading.Event] = {}
_lock = threading.Lock()
_last_persist_times: Dict[str, float] = {}
_downloads_initialized = False

_SOCIAL_MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "music.youtube.com",
    "instagram.com", "tiktok.com", "vt.tiktok.com", "vm.tiktok.com",
    "spotify.com", "soundcloud.com", "vimeo.com",
    "x.com", "twitter.com", "facebook.com", "fb.watch", "twitch.tv",
)
_DIRECT_FILE_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts",
    ".zip", ".apk", ".iso", ".bin", ".tar", ".gz", ".7z",
}


class _DownloadInterrupted(Exception):
    """Raised inside progress hooks when a task is paused or deleted."""


def _public_task_copy(task: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _persist_task(task: Optional[Dict[str, Any]], force: bool = False) -> None:
    if not task or not task.get("id"):
        return
    task_id = str(task["id"])
    now = time.monotonic()
    if not force and (now - _last_persist_times.get(task_id, 0.0)) < 2.0:
        return
    _last_persist_times[task_id] = now
    try:
        import database
        database.upsert_download_task(dict(task))
    except Exception as err:
        logger.debug(f"Could not persist download task {task_id}: {err}")


def init_downloads() -> None:
    """Hydrates download tasks from SQLite on server startup."""
    global _downloads_initialized
    try:
        import database
        rows = database.get_download_tasks()
    except Exception as err:
        logger.debug(f"Skipping download task hydration: {err}")
        _downloads_initialized = True
        return

    with _lock:
        for row in rows:
            tid = row.get("id")
            if not tid or tid in _download_tasks:
                continue
            if row.get("status") in ("pending", "downloading", "processing", "queued"):
                row["status"] = "paused"
                row["speed"] = 0
                row["eta"] = None
                if not row.get("error"):
                    row["error"] = "Paused after server restart. Tap Resume to continue."
                try:
                    import database
                    database.upsert_download_task(row)
                except Exception:
                    pass
            _download_tasks[tid] = row
        _downloads_initialized = True


def _parse_timestamp_seconds(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(f"Invalid timestamp '{text}'. Use seconds or MM:SS / HH:MM:SS.")
        try:
            total = 0.0
            for part in parts:
                total = total * 60.0 + float(part)
            return max(0.0, total)
        except ValueError as err:
            raise ValueError(f"Invalid timestamp '{text}'.") from err
    try:
        return max(0.0, float(text))
    except ValueError as err:
        raise ValueError(f"Invalid timestamp '{text}'.") from err


def _is_direct_http_candidate(url: str, clip_start: Optional[str] = None, clip_end: Optional[str] = None, subtitles: bool = False, download_playlist: bool = False) -> bool:
    if clip_start or clip_end or subtitles or download_playlist:
        return False
    lower = url.strip().lower()
    if not lower.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlparse(lower)
    host = (parsed.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _SOCIAL_MEDIA_HOSTS):
        return False
    ext = pathlib.Path(parsed.path).suffix.lower()
    if ext in (".m3u8", ".mpd"):
        return False
    if ext in _DIRECT_FILE_EXTENSIONS:
        return True
    return False


def _terminate_process(process: Optional[subprocess.Popen]) -> None:
    if not process or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
    except Exception:
        pass


def _record_tracked_file(task: Dict[str, Any], path_str: Optional[str]) -> None:
    if not path_str:
        return
    tracked = task.setdefault("_tracked_files", [])
    if path_str not in tracked:
        tracked.append(path_str)


def _cleanup_partial_files(task: Dict[str, Any]) -> None:
    destination = task.get("destination", "server")
    target_dir_str = task.get("target_dir")
    if not target_dir_str:
        return
    target_dir = pathlib.Path(target_dir_str)

    if destination == "client":
        shutil.rmtree(target_dir, ignore_errors=True)
        return

    if not target_dir.exists() or not target_dir.is_dir():
        return

    existing_files: Set[str] = set(task.get("_existing_files") or [])
    tracked_files: List[str] = list(task.get("_tracked_files") or [])
    if task.get("file_path"):
        tracked_files.append(str(task["file_path"]))
    video_id = str(task.get("_video_id") or "").strip()

    candidates: Set[pathlib.Path] = set()
    for raw_path in tracked_files:
        try:
            p = pathlib.Path(raw_path)
            candidates.add(p)
            for suffix in (".part", ".ytdl", ".temp"):
                candidates.add(pathlib.Path(str(p) + suffix))
            stem = p.stem
            if stem:
                for match in target_dir.glob(f"{glob.escape(stem)}*"):
                    candidates.add(match)
        except Exception:
            continue

    if video_id:
        for match in target_dir.glob(f"*[{glob.escape(video_id)}]*"):
            candidates.add(match)

    target_resolved = target_dir.resolve()
    for candidate in candidates:
        try:
            if candidate.name in existing_files:
                continue
            resolved = candidate.resolve()
            if resolved.parent != target_resolved:
                continue
            if resolved.is_file():
                resolved.unlink(missing_ok=True)
        except Exception:
            continue


def _youtube_runtime():
    if importlib.util.find_spec("yt_dlp_ejs") is None:
        raise RuntimeError('YouTube support is incomplete. On the server, run: python -m pip install -U "yt-dlp[default]"')
    for name, executable, minimum in (("deno", shutil.which("deno"), (2, 3)), ("node", NODE_PATH, (22, 0))):
        if not executable:
            continue
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5)
            match = re.search(r"(\d+)\.(\d+)", result.stdout)
            if result.returncode == 0 and match and tuple(map(int, match.groups())) >= minimum:
                return {name: {"path": executable}}
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("YouTube needs a supported JavaScript runtime on the server. Install Node.js 22+ or Deno 2.3+ and restart MediaPlayer.")

def _resolve_media_url(raw_input: str) -> Tuple[str, Optional[str]]:
    """
    Resolves input into a yt-dlp compatible URL or search query.
    Handles:
    - Spotify URLs: Scrapes public oEmbed / title tag and converts to ytsearch1:{artist} {track}
    - Plain text search queries: Automatically prefixes with ytsearch1:
    - Standard URLs (YouTube, SoundCloud, TikTok, Vimeo, etc.): Left intact
    """
    text = raw_input.strip()
    if not text:
        return text, None

    # Case 1: Plain text search query (no http scheme)
    if not text.startswith(("http://", "https://")):
        return f"ytsearch1:{text}", text

    # Case 2: Spotify link (Spotify audio files have DRM; resolve to high-quality audio search)
    if "spotify.com" in text or text.startswith("spotify:"):
        clean_url = text.split("?")[0]
        resolved_title = None
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}

        # Attempt 1: Fetch public Spotify embed page (contains pristine unauthenticated JSON metadata)
        try:
            parts = clean_url.rstrip("/").split("/")
            if len(parts) >= 2:
                media_type = parts[-2] # e.g. track, album, playlist
                media_id = parts[-1]
                embed_url = f"https://open.spotify.com/embed/{media_type}/{media_id}"
                req = urllib.request.Request(embed_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    artists = re.findall(r'\"artists\":\[\{\"name\":\"([^\"]+)\"', html)
                    titles = re.findall(r'\"name\":\"([^\"]+)\"', html)
                    artist = artists[0].strip() if artists else ""
                    title = titles[0].strip() if titles else ""
                    if title and artist and "Spotify" not in artist and "Web Player" not in title:
                        resolved_title = f"{artist} - {title}"
                    elif title and "Spotify" not in title and "Web Player" not in title:
                        resolved_title = title
        except Exception as e:
            logger.debug(f"Spotify embed metadata fetch failed: {e}")

        # Attempt 2: Spotify public oEmbed endpoint
        if not resolved_title:
            try:
                oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(clean_url)}"
                req = urllib.request.Request(oembed_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_title = data.get("title", "").strip()
                    if raw_title and "Spotify" not in raw_title and "Web Player" not in raw_title:
                        resolved_title = raw_title
            except Exception as e:
                logger.debug(f"Spotify oEmbed fetch failed: {e}")

        # Attempt 3: Scrape main webpage title
        if not resolved_title:
            try:
                req = urllib.request.Request(clean_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    m = re.search(r"<title>([^<]+)</title>", html)
                    if m:
                        t = m.group(1).replace(" | Spotify", "").strip()
                        for sep in [" - song and lyrics by ", " - song by ", " - single by ", " - album by ", " - track by "]:
                            if sep in t:
                                song, artist = t.split(sep, 1)
                                resolved_title = f"{artist.strip()} - {song.strip()}"
                                break
                        if not resolved_title and t and "Spotify" not in t and "Web Player" not in t:
                            resolved_title = t
            except Exception as e:
                logger.debug(f"Spotify webpage title scrape failed: {e}")

        if resolved_title:
            logger.info(f"Resolved Spotify link '{clean_url}' -> search '{resolved_title}'")
            return f"ytsearch1:{resolved_title}", resolved_title
        else:
            fallback_query = clean_url.rstrip("/").split("/")[-1]
            return f"ytsearch1:{fallback_query}", fallback_query

    # Case 3: Standard direct media link (YouTube, Vimeo, etc.)
    return text, None

def get_download_status(task_id: Optional[str] = None) -> Any:
    with _lock:
        if task_id:
            task = _download_tasks.get(task_id)
            return _public_task_copy(task) if task else None
        return [_public_task_copy(t) for t in _download_tasks.values()]

def get_download_file(task_id: str) -> Optional[pathlib.Path]:
    with _lock:
        task = _download_tasks.get(task_id)
        if not task:
            return None
        if task.get("status") != "completed":
            return None
        fpath = task.get("file_path")
        if fpath:
            p = pathlib.Path(fpath)
            if p.exists():
                return p
    return None

def _progress_hook(d: Dict[str, Any], task_id: str):
    with _lock:
        if task_id not in _download_tasks:
            raise _DownloadInterrupted("Download was deleted.")
        task = _download_tasks[task_id]
        if task.get("status") in ("paused", "cancelled"):
            raise _DownloadInterrupted(f"Download {task.get('status')}.")
        info = d.get('info_dict') or {}
        if info.get('title'):
            task['title'] = info['title']
        if info.get('id'):
            task['_video_id'] = str(info['id'])
        _record_tracked_file(task, d.get("filename"))
        _record_tracked_file(task, d.get("tmpfilename"))

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            task["downloaded_bytes"] = int(downloaded)
            task["total_bytes"] = int(total)
            if total > 0:
                percent = round((downloaded / total) * 100, 1)
                task["progress"] = percent
            task["status"] = "downloading"
            task["speed"] = d.get("speed", 0)
            task["eta"] = d.get("eta", 0)
            _persist_task(task, force=False)

        elif d.get("status") == "finished":
            task["status"] = "processing"
            task["progress"] = 100
            task["speed"] = 0
            task["eta"] = None
            _persist_task(task, force=True)


def _perform_chunked_http_download(
    task_id: str,
    url: str,
    target_dir: pathlib.Path,
    media_format: str,
    destination: str,
    owner_id: str,
    owner_name: str,
    target_playlist_id: Optional[str],
    custom_headers: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None,
    rate_limit_kbps: int = 0,
) -> bool:
    """
    Attempts a direct multi-threaded HTTP Range download writing directly into a single
    pre-allocated .part file (no post-merge step). Returns True if handled, False to
    fall back to yt-dlp.
    """
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    if custom_headers:
        for k, v in custom_headers.items():
            if k and v is not None:
                req_headers[str(k)] = str(v)

    opener = urllib.request.build_opener()
    if proxy_url and str(proxy_url).strip():
        p = str(proxy_url).strip()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))

    try:
        head_req = urllib.request.Request(url, method="HEAD", headers=req_headers)
        with opener.open(head_req, timeout=15) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or "mpegurl" in content_type or "dash+xml" in content_type:
                return False
            content_length = int(resp.headers.get("Content-Length") or 0)
            accept_ranges = "bytes" in (resp.headers.get("Accept-Ranges") or "").lower()
            disposition = resp.headers.get("Content-Disposition") or ""
    except Exception:
        return False

    if content_length <= 0:
        return False

    filename = ""
    disp_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', disposition, re.IGNORECASE)
    if disp_match:
        filename = urllib.parse.unquote(disp_match.group(1).strip())
    if not filename:
        filename = pathlib.Path(urllib.parse.urlparse(url).path).name or f"download_{task_id[:8]}"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip() or f"download_{task_id[:8]}"

    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / filename
    part_path = target_dir / f"{filename}.part"
    meta_path = target_dir / f"{filename}.part.meta.json"

    with _lock:
        task = _download_tasks.get(task_id)
        if not task or task.get("status") in ("paused", "cancelled"):
            raise _DownloadInterrupted("Paused or cancelled.")
        task["title"] = pathlib.Path(filename).stem
        task["total_bytes"] = content_length
        task["status"] = "downloading"
        _record_tracked_file(task, str(final_path))
        _record_tracked_file(task, str(part_path))
        _record_tracked_file(task, str(meta_path))
        _persist_task(task, force=True)

    num_chunks = 4 if (accept_ranges and content_length >= 2 * 1024 * 1024) else 1
    ranges: List[List[int]] = []
    if meta_path.exists() and part_path.exists() and part_path.stat().st_size == content_length:
        try:
            saved_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if saved_meta.get("total_bytes") == content_length and saved_meta.get("url") == url:
                ranges = saved_meta.get("ranges") or []
        except Exception:
            ranges = []

    if not ranges:
        chunk_size = content_length // num_chunks
        for i in range(num_chunks):
            start = i * chunk_size
            end = (content_length - 1) if i == num_chunks - 1 else ((i + 1) * chunk_size - 1)
            ranges.append([start, end, start])  # [start, end, current_offset]
        with open(part_path, "wb") as f:
            f.truncate(content_length)
        meta_path.write_text(json.dumps({"url": url, "total_bytes": content_length, "ranges": ranges}), encoding="utf-8")

    file_lock = threading.Lock()
    meta_lock = threading.Lock()
    errors: List[Exception] = []
    start_time = time.monotonic()
    initial_done = sum(max(0, r[2] - r[0]) for r in ranges)

    def _is_interrupted() -> bool:
        with _lock:
            cur = _download_tasks.get(task_id)
            return not cur or cur.get("status") in ("paused", "cancelled")

    def _save_meta():
        with meta_lock:
            try:
                meta_path.write_text(json.dumps({"url": url, "total_bytes": content_length, "ranges": ranges}), encoding="utf-8")
            except Exception:
                pass

    def _update_progress():
        done_bytes = sum(max(0, r[2] - r[0]) for r in ranges)
        elapsed = max(0.1, time.monotonic() - start_time)
        speed = max(0.0, (done_bytes - initial_done) / elapsed)
        remaining = max(0, content_length - done_bytes)
        eta = int(remaining / speed) if speed > 0 else None
        pct = round((done_bytes / content_length) * 100, 1) if content_length else 0.0
        with _lock:
            cur = _download_tasks.get(task_id)
            if cur and cur.get("status") not in ("paused", "cancelled"):
                cur["downloaded_bytes"] = done_bytes
                cur["total_bytes"] = content_length
                cur["progress"] = min(99.9, pct)
                cur["speed"] = speed
                cur["eta"] = eta
                _persist_task(cur, force=False)

    per_worker_rate = (rate_limit_kbps * 1024 / max(1, num_chunks)) if rate_limit_kbps > 0 else 0

    def _download_range(idx: int):
        r = ranges[idx]
        if r[2] > r[1]:
            return
        headers = dict(req_headers)
        if accept_ranges:
            headers["Range"] = f"bytes={r[2]}-{r[1]}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=30) as resp:
                chunk_start_ts = time.monotonic()
                bytes_since_ts = 0
                with open(part_path, "r+b") as fh:
                    while r[2] <= r[1]:
                        if _is_interrupted():
                            return
                        to_read = min(65536, r[1] - r[2] + 1)
                        buf = resp.read(to_read)
                        if not buf:
                            break
                        with file_lock:
                            fh.seek(r[2])
                            fh.write(buf)
                        r[2] += len(buf)
                        bytes_since_ts += len(buf)
                        if per_worker_rate > 0:
                            expected = bytes_since_ts / per_worker_rate
                            actual = time.monotonic() - chunk_start_ts
                            if expected > actual:
                                time.sleep(min(0.5, expected - actual))
                        _update_progress()
                        if bytes_since_ts >= 512 * 1024:
                            _save_meta()
                            bytes_since_ts = 0
                            chunk_start_ts = time.monotonic()
        except Exception as exc:
            errors.append(exc)
        finally:
            _save_meta()

    threads = [Thread(target=_download_range, args=(i,), daemon=True) for i in range(len(ranges))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if _is_interrupted():
        raise _DownloadInterrupted("Paused or cancelled.")
    if errors:
        raise errors[0]

    total_written = sum(max(0, r[2] - r[0]) for r in ranges)
    if total_written < content_length:
        raise RuntimeError(f"Incomplete download ({total_written}/{content_length} bytes).")

    os.replace(part_path, final_path)
    meta_path.unlink(missing_ok=True)

    song_id = None
    raw_title = final_path.stem
    artist_name = ""
    if destination == "server" and final_path.suffix.lower() in (config.AUDIO_EXTENSIONS | config.VIDEO_EXTENSIONS):
        is_vid = final_path.suffix.lower() in config.VIDEO_EXTENSIONS
        media_item = scanner.parse_media_file(
            final_path,
            folder_type="video" if is_vid else ("music" if media_format == "audio" else "video"),
            owner_id=owner_id,
            owner_name=owner_name,
        )
        if media_item:
            import database
            database.upsert_song(media_item)
            song_id = media_item["id"]
            raw_title = media_item["title"]
            artist_name = media_item.get("artist", "")
            if target_playlist_id and song_id:
                try:
                    database.add_song_to_playlist(target_playlist_id, song_id)
                except Exception:
                    pass

    with _lock:
        cur = _download_tasks.get(task_id)
        if not cur or cur.get("status") in ("paused", "cancelled"):
            return True
        cur["title"] = raw_title
        cur["artist"] = artist_name
        cur["song_id"] = song_id
        cur["file_path"] = str(final_path.resolve())
        cur["download_url"] = f"/api/downloads/{task_id}/file" if destination == "client" else (f"/api/stream/{song_id}?download=true" if song_id else f"/api/downloads/{task_id}/file")
        cur["status"] = "completed"
        cur["progress"] = 100
        cur["downloaded_bytes"] = content_length
        cur["total_bytes"] = content_length
        cur["speed"] = 0
        cur["eta"] = 0
        cur["error"] = None
        _persist_task(cur, force=True)

    if destination == "server":
        Thread(target=scanner.scan_library, daemon=True).start()
    return True

def _perform_download(
    task_id: str,
    raw_url: str,
    media_format: str,
    quality: str,
    target_dir: pathlib.Path,
    destination: str = "server",
    owner_id: str = "host",
    owner_name: str = "Host",
    cookies_browser: Optional[str] = None,
    target_playlist_id: Optional[str] = None,
    clip_start: Optional[str] = None,
    clip_end: Optional[str] = None,
    subtitles: bool = False,
    custom_headers: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None,
):
    task = _download_tasks[task_id]
    target_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(target_dir / "%(title).100B [%(id)s].%(ext)s")

    current_settings = {}
    try:
        current_settings = settings.load_settings()
    except Exception:
        current_settings = {}
    rate_limit_kbps = int(current_settings.get("download_rate_limit_kbps") or 0)

    if _is_direct_http_candidate(raw_url, clip_start=clip_start, clip_end=clip_end, subtitles=subtitles, download_playlist=False):
        try:
            handled = _perform_chunked_http_download(
                task_id=task_id,
                url=raw_url,
                target_dir=target_dir,
                media_format=media_format,
                destination=destination,
                owner_id=owner_id,
                owner_name=owner_name,
                target_playlist_id=target_playlist_id,
                custom_headers=custom_headers,
                proxy_url=proxy_url,
                rate_limit_kbps=rate_limit_kbps,
            )
            if handled:
                return
        except _DownloadInterrupted:
            logger.info(f"Direct HTTP download task {task_id} interrupted.")
            return
        except Exception as direct_err:
            logger.warning(f"Direct HTTP chunked download failed ({direct_err}), falling back to yt-dlp...")

    # Resolve URL (e.g. Spotify DRM -> YouTube search, plain query -> ytsearch1)
    target_url, resolved_hint = _resolve_media_url(raw_url)
    hostname = (urllib.parse.urlparse(target_url).hostname or "").lower()
    is_youtube = target_url.startswith("ytsearch") or hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com")
    youtube_runtime = _youtube_runtime() if is_youtube else None
    if resolved_hint:
        with _lock:
            task["title"] = resolved_hint

    # Base configuration for yt-dlp
    ydl_opts: Dict[str, Any] = {
        "outtmpl": out_template,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [lambda d: _progress_hook(d, task_id)],
        "writethumbnail": True,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "noplaylist": True,
        "playlistend": 1,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }

    if rate_limit_kbps > 0:
        ydl_opts["ratelimit"] = rate_limit_kbps * 1024
    if proxy_url and str(proxy_url).strip():
        ydl_opts["proxy"] = str(proxy_url).strip()
    if custom_headers:
        ydl_opts["http_headers"] = {str(k): str(v) for k, v in custom_headers.items() if k and v is not None}

    start_sec = _parse_timestamp_seconds(clip_start)
    end_sec = _parse_timestamp_seconds(clip_end)
    if start_sec is not None or end_sec is not None:
        from yt_dlp.utils import download_range_func
        s_val = start_sec if start_sec is not None else 0.0
        e_val = end_sec if end_sec is not None else float("inf")
        ydl_opts["download_ranges"] = download_range_func(None, [(s_val, e_val)])
        ydl_opts["force_keyframes_at_cuts"] = True

    if subtitles:
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = True
        ydl_opts["subtitleslangs"] = ["en.*", "en"]

    # Browser cookies: unlocks login-gated Instagram reels/posts, age gates & bypasses bot checks
    if cookies_browser and str(cookies_browser).strip().lower() not in ("none", "off", "disabled", "false", ""):
        ydl_opts["cookiesfrombrowser"] = (str(cookies_browser).strip().lower(),)

    if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    if NODE_PATH and os.path.exists(NODE_PATH):
        ydl_opts["js_runtimes"] = {"node": {"path": NODE_PATH}}
    if youtube_runtime:
        ydl_opts["js_runtimes"] = youtube_runtime

    if media_format == "audio":
        # Extract audio stream as MP3 with embedded tags and thumbnail
        if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    },
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata"},
                ],
            })
        else:
            ydl_opts.update({
                "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
            })
    else:
        # Video download
        if quality in ("4k", "2160p"):
            format_str = "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
        elif quality == "1080p":
            format_str = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif quality == "720p":
            format_str = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        else:
            format_str = "bestvideo+bestaudio/best"

        limit = "[height<=2160]" if quality in ("4k", "2160p") else "[height<=1080]" if quality == "1080p" else "[height<=720]" if quality == "720p" else ""
        if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
            format_str = f"bestvideo[ext=mp4][vcodec^=avc1]{limit}+bestaudio[ext=m4a]/best[ext=mp4]{limit}/" + format_str
        else:
            format_str = f"best[ext=mp4]{limit}/best{limit}"

        ydl_opts.update({
            "format": format_str,
            "merge_output_format": "mp4",
        })
        if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
            postprocessors = []
            if subtitles:
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})
            postprocessors.append({"key": "FFmpegMetadata"})
            ydl_opts["postprocessors"] = postprocessors

    def _locate_file(ydl_instance, entry_dict) -> Optional[pathlib.Path]:
        """Locates the final downloaded media file on disk."""
        try:
            base_target = ydl_instance.prepare_filename(entry_dict)
            p_base = pathlib.Path(base_target)
            if media_format == "audio":
                for ext in [".mp3", ".m4a", ".opus", ".flac", ".ogg", ".webm"]:
                    candidate = p_base.with_suffix(ext)
                    if candidate.exists():
                        return candidate
            else:
                for ext in [".mp4", ".mkv", ".webm"]:
                    candidate = p_base.with_suffix(ext)
                    if candidate.exists():
                        return candidate
            if p_base.exists():
                return p_base
        except Exception:
            pass

        # Never guess from other files in the shared library.
        return None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            
            # Unpack playlist / search result dict if needed
            if info and "entries" in info and info["entries"]:
                entries = list(info["entries"])
                entry = entries[0] if entries else info
            else:
                entry = info or {}

            with _lock:
                if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                    return
                if entry.get("id"):
                    task["_video_id"] = str(entry["id"])
                try:
                    _record_tracked_file(task, ydl.prepare_filename(entry))
                except Exception:
                    pass

            raw_title = entry.get("title") or resolved_hint or "Downloaded Media"
            artist_name = entry.get("artist") or entry.get("uploader") or ""
            downloaded_file = _locate_file(ydl, entry)
            if not downloaded_file:
                raise RuntimeError("The downloader returned no completed media file.")

            song_id = None
            if downloaded_file and downloaded_file.exists():
                if destination == "server":
                    media_item = scanner.parse_media_file(
                        downloaded_file,
                        folder_type="music" if media_format == "audio" else "video",
                        owner_id=owner_id,
                        owner_name=owner_name
                    )
                    if media_item:
                        import database
                        database.upsert_song(media_item)
                        song_id = media_item["id"]
                        raw_title = media_item["title"]
                        artist_name = media_item.get("artist", "")
                        if target_playlist_id and song_id:
                            try:
                                database.add_song_to_playlist(target_playlist_id, song_id)
                            except Exception as pl_err:
                                logger.warning(f"Failed to add to playlist {target_playlist_id}: {pl_err}")
                else:
                    raw_title = downloaded_file.stem

            with _lock:
                if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                    return
                task["title"] = raw_title
                task["artist"] = artist_name
                task["song_id"] = song_id
                task["file_path"] = str(downloaded_file.resolve()) if downloaded_file else None
                task["download_url"] = f"/api/downloads/{task_id}/file" if destination == "client" else (f"/api/stream/{song_id}?download=true" if song_id else None)
                task["status"] = "completed"
                task["progress"] = 100
                task["duration"] = entry.get("duration", 0)
                task["error"] = None
                _persist_task(task, force=True)

        if destination == "server":
            Thread(target=scanner.scan_library, daemon=True).start()

    except _DownloadInterrupted:
        logger.info(f"Download task {task_id} interrupted (paused or cancelled).")
        return
    except Exception as e:
        with _lock:
            if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                return
        logger.warning(f"Download with standard opts failed ({e}), attempting fallback stream download...")
        try:
            fallback_opts: Dict[str, Any] = {
                "outtmpl": out_template,
                "quiet": True,
                "socket_timeout": 20,
                "retries": 1,
                "extractor_retries": 1,
                "fragment_retries": 1,
                "noplaylist": True,
                "playlistend": 1,
                "no_warnings": True,
                "progress_hooks": [lambda d: _progress_hook(d, task_id)],
                "format": "bestaudio[ext=m4a]/bestaudio/best" if media_format == "audio" else "best[ext=mp4]/best",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            }
            if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
                fallback_opts["ffmpeg_location"] = FFMPEG_PATH
            if NODE_PATH and os.path.exists(NODE_PATH):
                fallback_opts["js_runtimes"] = {"node": {"path": NODE_PATH}}
            if youtube_runtime:
                fallback_opts["js_runtimes"] = youtube_runtime
            if cookies_browser and str(cookies_browser).strip().lower() not in ("none", "off", "disabled", "false", ""):
                if "cookie" not in str(e).lower():
                    fallback_opts["cookiesfrombrowser"] = (str(cookies_browser).strip().lower(),)
                else:
                    logger.warning(f"Browser cookies from '{cookies_browser}' caused extraction failure ({e}). Retrying fallback without cookies.")

            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                info = ydl.extract_info(target_url, download=True)
                
                if info and "entries" in info and info["entries"]:
                    entries = list(info["entries"])
                    entry = entries[0] if entries else info
                else:
                    entry = info or {}

                with _lock:
                    if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                        return
                    if entry.get("id"):
                        task["_video_id"] = str(entry["id"])
                    try:
                        _record_tracked_file(task, ydl.prepare_filename(entry))
                    except Exception:
                        pass

                raw_title = entry.get("title") or resolved_hint or "Downloaded Media"
                artist_name = entry.get("artist") or entry.get("uploader") or ""
                downloaded_file = _locate_file(ydl, entry)
                if not downloaded_file:
                    raise RuntimeError("The downloader returned no completed media file.")

                song_id = None
                if downloaded_file and downloaded_file.exists():
                    if destination == "server":
                        media_item = scanner.parse_media_file(
                            downloaded_file,
                            folder_type="music" if media_format == "audio" else "video",
                            owner_id=owner_id,
                            owner_name=owner_name
                        )
                        if media_item:
                            import database
                            database.upsert_song(media_item)
                            song_id = media_item["id"]
                            raw_title = media_item["title"]
                            artist_name = media_item.get("artist", "")
                            if target_playlist_id and song_id:
                                try:
                                    database.add_song_to_playlist(target_playlist_id, song_id)
                                except Exception as pl_err:
                                    logger.warning(f"Failed to add to playlist {target_playlist_id}: {pl_err}")
                    else:
                        raw_title = downloaded_file.stem

                with _lock:
                    if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                        return
                    task["title"] = raw_title
                    task["artist"] = artist_name
                    task["song_id"] = song_id
                    task["file_path"] = str(downloaded_file.resolve()) if downloaded_file else None
                    task["download_url"] = f"/api/downloads/{task_id}/file" if destination == "client" else (f"/api/stream/{song_id}?download=true" if song_id else None)
                    task["status"] = "completed"
                    task["progress"] = 100
                    task["duration"] = entry.get("duration", 0)
                    task["error"] = None
                    _persist_task(task, force=True)

            if destination == "server":
                Thread(target=scanner.scan_library, daemon=True).start()

        except _DownloadInterrupted:
            logger.info(f"Download task {task_id} interrupted during fallback (paused or cancelled).")
            return
        except Exception as err:
            with _lock:
                if task_id not in _download_tasks or task.get("status") in ("paused", "cancelled"):
                    return
            logger.error(f"Download task {task_id} failed: {err}")
            clean_err = str(err)
            if "DRM" in clean_err:
                clean_err = "DRM protected stream. Try searching by song title."
            elif "Sign in to confirm" in clean_err or "bot" in clean_err.lower():
                clean_err = "YouTube requires sign-in or detected a bot check. Enable Browser Cookies (e.g. Chrome) in Downloader."
            elif "login" in clean_err.lower() and "instagram" in clean_err.lower():
                clean_err = "Instagram requires login to view this reel/post. Enable Browser Cookies (e.g. Chrome) in Downloader."
            elif "403" in clean_err:
                clean_err = "Access forbidden by media host (HTTP 403). Stream may be geo-restricted or require login cookies."
            
            with _lock:
                if task_id in _download_tasks and task.get("status") not in ("paused", "cancelled"):
                    task["status"] = "failed"
                    task["error"] = clean_err
                    if task["title"] == "Fetching metadata...":
                        task["title"] = resolved_hint or raw_url.split("?")[0].split("/")[-1] or "Download Failed"
                    _persist_task(task, force=True)

def _active_worker_count_locked() -> int:
    return sum(
        1 for t in _download_tasks.values()
        if t.get("status") in ("pending", "downloading", "processing")
    )


def _max_concurrent_downloads() -> int:
    try:
        val = int(settings.load_settings().get("max_concurrent_downloads") or 2)
        return max(1, min(10, val))
    except Exception:
        return 2


def _dispatch_queued_downloads() -> None:
    to_launch: List[Tuple[Any, ...]] = []
    max_conc = _max_concurrent_downloads()
    with _lock:
        active = _active_worker_count_locked()
        if active >= max_conc:
            return
        queued = [t for t in _download_tasks.values() if t.get("status") == "queued"]
        for task in queued:
            if active >= max_conc:
                break
            task["status"] = "pending"
            task["error"] = None
            _persist_task(task, force=True)
            to_launch.append((
                task["id"],
                task["url"],
                task["format"],
                task["quality"],
                pathlib.Path(task["target_dir"]),
                task.get("destination", "server"),
                task.get("owner_id", "host"),
                task.get("owner_name", "Host"),
                task.get("cookies_browser"),
            ))
            active += 1
    for args in to_launch:
        Thread(target=_run_download, args=args, daemon=True).start()


def _run_download(task_id, raw_url, media_format, quality, target_dir, destination="server", owner_id="host", owner_name="Host", cookies_browser=None):
    """Supervise an isolated worker so extractor hangs cannot leave pending tasks."""
    process = None
    done_event = threading.Event()
    with _lock:
        _worker_done_events[task_id] = done_event
        task_ref = _download_tasks.get(task_id)
        if not task_ref or task_ref.get("status") in ("paused", "cancelled"):
            done_event.set()
            return
        target_playlist_id = task_ref.get("target_playlist_id")
        clip_start = task_ref.get("clip_start")
        clip_end = task_ref.get("clip_end")
        subtitles = bool(task_ref.get("subtitles"))
        custom_headers = task_ref.get("headers")
        proxy_url = task_ref.get("proxy")
    def fail(message):
        with _lock:
            task = _download_tasks.get(task_id)
            if task and task.get("status") not in ("paused", "cancelled", "completed"):
                task.update(status="failed", error=message, title=task.get("title") or raw_url)
                _persist_task(task, force=True)
    try:
        if getattr(sys, "frozen", False):
            _perform_download(
                task_id, raw_url, media_format, quality, target_dir, destination,
                owner_id, owner_name, cookies_browser, target_playlist_id,
                clip_start, clip_end, subtitles, custom_headers, proxy_url,
            )
            return
        with _lock:
            task_ref = _download_tasks.get(task_id)
            if not task_ref or task_ref.get("status") in ("paused", "cancelled"):
                return
            initial = dict(task_ref)
        process = subprocess.Popen(
            [sys.executable, "-u", str(pathlib.Path(__file__).resolve()), "--download-worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with _lock:
            task_ref = _download_tasks.get(task_id)
            if not task_ref or task_ref.get("status") in ("paused", "cancelled"):
                _terminate_process(process)
                return
            _active_processes[task_id] = process
        process.stdin.write(json.dumps(initial) + "\n")
        process.stdin.close()
        def read_updates():
            for line in process.stdout:
                if not line.startswith("MEDIA_TASK:"):
                    continue
                try:
                    update = json.loads(line[len("MEDIA_TASK:"):])
                    with _lock:
                        cur = _download_tasks.get(task_id)
                        if cur and cur["status"] not in ("failed", "completed", "paused", "cancelled"):
                            cur.update(update)
                            _persist_task(cur, force=cur.get("status") in ("completed", "failed"))
                except (ValueError, TypeError):
                    continue
        reader = Thread(target=read_updates, daemon=True)
        reader.start()
        started = time.monotonic()
        while process.poll() is None:
            elapsed = time.monotonic() - started
            with _lock:
                cur = _download_tasks.get(task_id)
                status = cur["status"] if cur else "cancelled"
            if status in ("paused", "cancelled"):
                break
            if (status == "pending" and elapsed > 120) or elapsed > 1800:
                fail("The server timed out fetching metadata. Check internet access and the yt-dlp JavaScript runtime setup." if status == "pending" else "Download exceeded the 30-minute limit. Try a lower quality or shorter video.")
                return
            time.sleep(0.25)
        reader.join(timeout=3)
        with _lock:
            cur = _download_tasks.get(task_id)
            status = cur["status"] if cur else "cancelled"
        if status not in ("completed", "failed", "paused", "cancelled"):
            fail("The download worker stopped unexpectedly. Check the server's Python dependencies and folder permissions.")
    except Exception as error:
        logger.exception("Download worker failed")
        fail(f"Could not start download: {error}")
    finally:
        with _lock:
            if _active_processes.get(task_id) is process:
                _active_processes.pop(task_id, None)
        if process:
            _terminate_process(process)
            if process.stdout:
                try:
                    process.stdout.close()
                except Exception:
                    pass
        done_event.set()
        _dispatch_queued_downloads()


def _download_worker():
    task = json.loads(sys.stdin.readline())
    task_id = task["id"]
    _download_tasks[task_id] = task
    done = threading.Event()
    def emit():
        with _lock:
            print("MEDIA_TASK:" + json.dumps(dict(task)), flush=True)
    def report():
        while not done.wait(0.5):
            emit()
    Thread(target=report, daemon=True).start()
    try:
        _perform_download(
            task_id,
            task["url"],
            task["format"],
            task["quality"],
            pathlib.Path(task["target_dir"]),
            task["destination"],
            task["owner_id"],
            task["owner_name"],
            task.get("cookies_browser"),
            task.get("target_playlist_id"),
            task.get("clip_start"),
            task.get("clip_end"),
            bool(task.get("subtitles")),
            task.get("headers"),
            task.get("proxy"),
        )
    except Exception as error:
        with _lock:
            task.update(status="failed", error=f"Download failed: {error}")
    finally:
        done.set()
        emit()


def _expand_playlist_and_enqueue(
    parent_task_id: str,
    url: str,
    media_format: str,
    quality: str,
    destination: str,
    custom_folder: Optional[str],
    owner_id: str,
    owner_name: str,
    cookies_browser: Optional[str],
    target_playlist_id: Optional[str],
    subtitles: bool,
    headers: Optional[Dict[str, str]],
    proxy: Optional[str],
):
    try:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "socket_timeout": 20,
            "playlistend": 50,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        if proxy:
            ydl_opts["proxy"] = proxy
        if headers:
            ydl_opts["http_headers"] = headers
        if cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        entry_urls: List[str] = []
        playlist_title = ""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                playlist_title = str(info.get("title") or "").strip()
                raw_entries = info.get("entries")
                if raw_entries:
                    for entry in raw_entries:
                        if not entry:
                            continue
                        vid_id = str(entry.get("id") or "").strip()
                        e_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
                        if e_url and not e_url.startswith(("http://", "https://")) and vid_id:
                            e_url = f"https://www.youtube.com/watch?v={vid_id}"
                        elif not e_url and vid_id:
                            e_url = f"https://www.youtube.com/watch?v={vid_id}"
                        if e_url:
                            entry_urls.append(e_url)
                        if len(entry_urls) >= 50:
                            break

        if not entry_urls:
            entry_urls = [url]

        for entry_url in entry_urls:
            start_download(
                url=entry_url,
                media_format=media_format,
                quality=quality,
                destination=destination,
                custom_folder=custom_folder,
                owner_id=owner_id,
                owner_name=owner_name,
                cookies_browser=cookies_browser,
                target_playlist_id=target_playlist_id,
                subtitles=subtitles,
                download_playlist=False,
                headers=headers,
                proxy=proxy,
            )

        with _lock:
            parent = _download_tasks.get(parent_task_id)
            if parent:
                label = f"{playlist_title}: " if playlist_title else ""
                parent["title"] = f"{label}Queued {len(entry_urls)} playlist items"
                parent["status"] = "completed"
                parent["progress"] = 100
                parent["error"] = None
                _persist_task(parent, force=True)
    except Exception as err:
        with _lock:
            parent = _download_tasks.get(parent_task_id)
            if parent:
                parent["status"] = "failed"
                parent["error"] = f"Playlist extraction failed: {err}"
                _persist_task(parent, force=True)


def start_download(
    url: str,
    media_format: str = "audio",
    quality: str = "best",
    destination: str = "server",
    custom_folder: Optional[str] = None,
    owner_id: str = "host",
    owner_name: str = "Host",
    cookies_browser: Optional[str] = None,
    target_playlist_id: Optional[str] = None,
    clip_start: Optional[str] = None,
    clip_end: Optional[str] = None,
    subtitles: bool = False,
    download_playlist: bool = False,
    headers: Optional[Dict[str, str]] = None,
    proxy: Optional[str] = None,
) -> str:
    """
    Enqueues a download task in background and returns task_id.
    destination: "server" (saves to host media library) or "client" (saves to staging for direct browser download)
    cookies_browser: "chrome", "edge", "firefox", or None/empty
    """
    if media_format not in ("audio", "video") or destination not in ("server", "client"):
        raise ValueError("Choose Audio or Video and a valid download destination.")
    _parse_timestamp_seconds(clip_start)
    _parse_timestamp_seconds(clip_end)

    task_id = str(uuid.uuid4())
    current_settings = settings.load_settings()
    if destination == "client":
        target_dir = config.STAGING_DIR / f"client_{task_id}"
    elif custom_folder:
        target_dir = pathlib.Path(custom_folder)
    else:
        target_dir = settings.get_target_upload_folder(media_type="video" if media_format == "video" else "audio")

    # Resolve cookies browser choice: per-request option overrides setting default
    active_cookies_browser = cookies_browser if cookies_browser is not None else current_settings.get("download_browser_cookies", "")
    if active_cookies_browser:
        active_cookies_browser = str(active_cookies_browser).strip().lower()
        if active_cookies_browser in ("none", "off", "disabled", "false", ""):
            active_cookies_browser = None
    else:
        active_cookies_browser = None

    clean_headers = {str(k): str(v) for k, v in headers.items() if k and v is not None} if headers else None
    clean_proxy = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
    clean_clip_start = clip_start.strip() if isinstance(clip_start, str) and clip_start.strip() else None
    clean_clip_end = clip_end.strip() if isinstance(clip_end, str) and clip_end.strip() else None

    if download_playlist:
        with _lock:
            _download_tasks[task_id] = {
                "id": task_id,
                "url": url,
                "title": "Expanding playlist...",
                "format": media_format,
                "quality": quality,
                "destination": destination,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "cookies_browser": active_cookies_browser,
                "target_playlist_id": target_playlist_id,
                "status": "pending",
                "progress": 0,
                "speed": 0,
                "eta": None,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "target_dir": str(target_dir),
                "error": None,
                "download_playlist": True,
                "created_at": time.time(),
                "_existing_files": [],
                "_tracked_files": [],
                "_video_id": None,
            }
            _persist_task(_download_tasks[task_id], force=True)
        Thread(
            target=_expand_playlist_and_enqueue,
            args=(
                task_id, url, media_format, quality, destination, custom_folder,
                owner_id, owner_name, active_cookies_browser, target_playlist_id,
                bool(subtitles), clean_headers, clean_proxy,
            ),
            daemon=True,
        ).start()
        return task_id

    existing_files: List[str] = []
    if destination == "server" and target_dir.exists() and target_dir.is_dir():
        try:
            existing_files = [p.name for p in target_dir.iterdir()]
        except Exception:
            existing_files = []

    max_conc = max(1, min(10, int(current_settings.get("max_concurrent_downloads") or 2)))
    should_queue = False
    with _lock:
        if _active_worker_count_locked() >= max_conc:
            should_queue = True
        _download_tasks[task_id] = {
            "id": task_id,
            "url": url,
            "title": "Fetching metadata...",
            "format": media_format,
            "quality": quality,
            "destination": destination,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "cookies_browser": active_cookies_browser,
            "target_playlist_id": target_playlist_id,
            "status": "queued" if should_queue else "pending",
            "progress": 0,
            "speed": 0,
            "eta": None,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "target_dir": str(target_dir),
            "error": None,
            "clip_start": clean_clip_start,
            "clip_end": clean_clip_end,
            "subtitles": bool(subtitles),
            "download_playlist": False,
            "headers": clean_headers,
            "proxy": clean_proxy,
            "created_at": time.time(),
            "_existing_files": existing_files,
            "_tracked_files": [],
            "_video_id": None,
        }
        _persist_task(_download_tasks[task_id], force=True)

    if not should_queue:
        thread = Thread(
            target=_run_download,
            args=(task_id, url, media_format, quality, target_dir, destination, owner_id, owner_name, active_cookies_browser),
            daemon=True
        )
        thread.start()
    return task_id


def pause_download(task_id: str) -> Dict[str, Any]:
    """Pauses an active download task while keeping partial files so it can be resumed."""
    with _lock:
        task = _download_tasks.get(task_id)
        if not task:
            raise KeyError("Download task not found.")
        status = task.get("status")
        if status == "paused":
            return _public_task_copy(task)
        if status in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot pause a task that is {status}.")
        task["status"] = "paused"
        task["speed"] = 0
        task["eta"] = None
        _persist_task(task, force=True)
        proc = _active_processes.get(task_id)
        done_event = _worker_done_events.get(task_id)
        result = _public_task_copy(task)

    _terminate_process(proc)
    if done_event:
        done_event.wait(timeout=3)
    _dispatch_queued_downloads()
    return result


def resume_download(task_id: str) -> Dict[str, Any]:
    """Resumes a paused or failed download task from partial files."""
    with _lock:
        task = _download_tasks.get(task_id)
        if not task:
            raise KeyError("Download task not found.")
        if task.get("status") not in ("paused", "failed"):
            raise ValueError("Only paused or failed downloads can be resumed.")
        done_event = _worker_done_events.get(task_id)

    if done_event:
        done_event.wait(timeout=3)

    max_conc = _max_concurrent_downloads()
    should_queue = False
    with _lock:
        task = _download_tasks.get(task_id)
        if not task:
            raise KeyError("Download task not found.")
        if task.get("status") not in ("paused", "failed"):
            raise ValueError("Only paused or failed downloads can be resumed.")
        if _active_worker_count_locked() >= max_conc:
            should_queue = True
        task["status"] = "queued" if should_queue else "pending"
        task["error"] = None
        task["speed"] = 0
        task["eta"] = None
        _persist_task(task, force=True)
        args = (
            task_id,
            task["url"],
            task["format"],
            task["quality"],
            pathlib.Path(task["target_dir"]),
            task.get("destination", "server"),
            task.get("owner_id", "host"),
            task.get("owner_name", "Host"),
            task.get("cookies_browser"),
        )
        result = _public_task_copy(task)

    if not should_queue:
        Thread(target=_run_download, args=args, daemon=True).start()
    return result


def edit_download(
    task_id: str,
    url: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    proxy: Optional[str] = None,
) -> Dict[str, Any]:
    """Updates URL, custom headers, or proxy on a paused, failed, or queued download task."""
    with _lock:
        task = _download_tasks.get(task_id)
        if not task:
            raise KeyError("Download task not found.")
        if task.get("status") not in ("paused", "failed", "queued"):
            raise ValueError("Pause the download before editing its URL, headers, or proxy.")
        if url is not None and url.strip():
            task["url"] = url.strip()
        if headers is not None:
            task["headers"] = {str(k): str(v) for k, v in headers.items() if k and v is not None} or None
        if proxy is not None:
            task["proxy"] = proxy.strip() or None
        _persist_task(task, force=True)
        return _public_task_copy(task)


def delete_download(task_id: str) -> bool:
    """Stops an active/paused download task, removes partial files, and deletes it from the queue."""
    with _lock:
        task = _download_tasks.pop(task_id, None)
        if not task:
            return False
        was_completed = task.get("status") == "completed"
        task["status"] = "cancelled"
        proc = _active_processes.get(task_id)
        done_event = _worker_done_events.get(task_id)

    _terminate_process(proc)
    if done_event:
        done_event.wait(timeout=3)

    if not was_completed or task.get("destination") == "client":
        _cleanup_partial_files(task)
    try:
        import database
        database.delete_download_task(task_id)
    except Exception:
        pass
    _dispatch_queued_downloads()
    return True

def search_youtube(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Searches YouTube using yt-dlp (metadata only, no download) and returns relevant results.
    """
    clean = (query or "").strip()
    if not clean:
        return []

    target_url, resolved_hint = _resolve_media_url(clean)
    if resolved_hint:
        target = f"ytsearch{limit}:{resolved_hint}"
    elif target_url.startswith("ytsearch"):
        _, _, search_term = target_url.partition(":")
        target = f"ytsearch{limit}:{search_term}"
    else:
        target = target_url

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 12,
        "noplaylist": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    if NODE_PATH and os.path.exists(NODE_PATH):
        ydl_opts["js_runtimes"] = {"node": {"path": NODE_PATH}}

    results: List[Dict[str, Any]] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
        if not info:
            return []
        raw_entries = info.get("entries") if "entries" in info else [info]
        for entry in raw_entries or []:
            if not entry:
                continue
            vid_id = str(entry.get("id") or "").strip()
            watch_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
            if watch_url and not watch_url.startswith(("http://", "https://")) and vid_id:
                watch_url = f"https://www.youtube.com/watch?v={vid_id}"
            elif not watch_url and vid_id:
                watch_url = f"https://www.youtube.com/watch?v={vid_id}"
            if not watch_url:
                continue

            thumbs = entry.get("thumbnails") or []
            thumb = entry.get("thumbnail")
            if not thumb and isinstance(thumbs, list) and thumbs:
                thumb = thumbs[-1].get("url")
            if not thumb and vid_id:
                thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

            results.append({
                "id": vid_id or watch_url,
                "title": str(entry.get("title") or "Untitled Video").strip(),
                "channel": str(entry.get("channel") or entry.get("uploader") or "YouTube").strip(),
                "duration": float(entry.get("duration") or 0),
                "thumbnail": thumb,
                "url": watch_url,
                "source": "youtube",
            })
            if len(results) >= limit:
                break

    return results


def clear_downloads() -> None:
    """Removes completed, failed, or cancelled download tasks from the queue."""
    with _lock:
        global _download_tasks
        _download_tasks = {
            tid: t for tid, t in _download_tasks.items()
            if t.get("status") in ("downloading", "pending", "processing", "paused", "queued")
        }
    try:
        import database
        database.clear_inactive_download_tasks()
    except Exception:
        pass


if __name__ == "__main__" and "--download-worker" in sys.argv:
    _download_worker()


