import os
import re
import urllib.parse
import pathlib
import hashlib
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Generator, Optional
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

_conversion_slots = threading.BoundedSemaphore(2)
_conversion_jobs = {}
_jobs_lock = threading.Lock()

_hls_slots = threading.BoundedSemaphore(2)
_hls_jobs = {}
_hls_lock = threading.Lock()

HLS_QUALITY_HEIGHTS = {
    "original": 0,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}


def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Compatible playback needs FFmpeg on the server. Install FFmpeg or use VLC.",
        )


def _stat_media_file(file_path: str) -> os.stat_result:
    try:
        stat = os.stat(file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file missing. Check the server drive and rescan.")
    except PermissionError:
        raise HTTPException(status_code=403, detail="The server cannot read this file. Check folder permissions.")
    if not stat.st_size:
        raise HTTPException(status_code=422, detail="This media file is empty.")
    return stat


def compatibility_job(file_path: str, is_video: bool, start: bool = False):
    """Short polling requests let preparation survive a remote proxy timeout."""
    try:
        stat = os.stat(file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file missing. Check the server drive and rescan.")
    except PermissionError:
        raise HTTPException(status_code=403, detail="The server cannot read this file. Check folder permissions.")
    key = (file_path, stat.st_mtime_ns, stat.st_size, is_video)
    with _jobs_lock:
        for old_key, job in list(_conversion_jobs.items()):
            if job["status"] != "preparing" and time.monotonic() - job["updated"] > 3600:
                del _conversion_jobs[old_key]
        existing = _conversion_jobs.get(key)
        if existing and (not start or existing["status"] in ("preparing", "ready")):
            return {k: v for k, v in existing.items() if k != "updated"}
        if not start:
            return {"status": "idle"}
        if sum(job["status"] == "preparing" for job in _conversion_jobs.values()) >= 2:
            raise HTTPException(status_code=503, detail="The server is preparing other files. Try again shortly.")
        _conversion_jobs[key] = {"status": "preparing", "updated": time.monotonic()}

    def worker():
        try:
            compatible_media(file_path, is_video)
            result = {"status": "ready"}
        except HTTPException as error:
            result = {"status": "failed", "detail": error.detail}
        except Exception:
            result = {"status": "failed", "detail": "The server could not prepare this file. Try VLC."}
        with _jobs_lock:
            _conversion_jobs[key] = {**result, "updated": time.monotonic()}

    threading.Thread(target=worker, daemon=True, name="CompatiblePlayback").start()
    return {"status": "preparing"}

CHUNK_SIZE = 512 * 1024  # 512KB chunks for smooth buffering of audio and 1080p/4K video

def parse_range_header(range_header: str, file_size: int):
    """
    Parses 'Range: bytes=start-end' header and returns (start, end, content_length).
    """
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        return 0, file_size - 1, file_size
    
    start_str, end_str = match.groups()
    if not start_str and end_str:
        start = max(0, file_size - int(end_str))
        end = file_size - 1
    elif start_str:
        start = int(start_str)
        end = min(int(end_str), file_size - 1) if end_str else file_size - 1
    else:
        start, end = file_size, file_size - 1

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    content_length = end - start + 1
    return start, end, content_length

def file_chunk_generator(file_path: str, start: int, end: int, chunk_size: int = CHUNK_SIZE) -> Generator[bytes, None, None]:
    """Reads a slice of an audio or video file in chunks without loading everything into memory."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = f.read(read_size)
            if not data:
                break
            remaining -= len(data)
            yield data

def stream_audio(file_path: str, request: Request, mime_type: str = "audio/mpeg", download_filename: Optional[str] = None) -> StreamingResponse:
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Media file not found")

    try:
        with open(file_path, "rb") as source:
            file_size = os.fstat(source.fileno()).st_size
    except PermissionError:
        raise HTTPException(status_code=403, detail="The server cannot read this media file. Check its folder permissions.")
    if not file_size:
        raise HTTPException(status_code=422, detail="This media file is empty.")
    range_header = request.headers.get("range")
    is_download = request.query_params.get("download") == "true" or download_filename is not None

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_type,
    }

    if is_download:
        safe_name = download_filename or os.path.basename(file_path)
        encoded_name = urllib.parse.quote(safe_name)
        ascii_fallback = safe_name.encode('ascii', 'ignore').decode('ascii').replace('"', '') or 'media_file'
        headers["Content-Disposition"] = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_name}'

    if range_header:
        start, end, content_length = parse_range_header(range_header, file_size)
        headers.update({
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
        })
        return StreamingResponse(
            file_chunk_generator(file_path, start, end),
            status_code=206,
            headers=headers
        )
    else:
        headers["Content-Length"] = str(file_size)
        return StreamingResponse(
            file_chunk_generator(file_path, 0, file_size - 1),
            status_code=200,
            headers=headers
        )


def compatible_media(file_path: str, is_video: bool):
    """Cache a browser-compatible copy; never modify the original media file."""
    import config
    try:
        with open(file_path, "rb") as source:
            stat = os.fstat(source.fileno())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file missing. Check the server drive and rescan the library.")
    except PermissionError:
        raise HTTPException(status_code=403, detail="The server cannot read this file. Check folder permissions.")
    if not stat.st_size:
        raise HTTPException(status_code=422, detail="This media file is empty.")
    key = hashlib.sha256(f"v1:{file_path}:{stat.st_mtime_ns}:{stat.st_size}:{is_video}".encode()).hexdigest()
    cache = config.CACHE_DIR / "playback"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + (".mp4" if is_video else ".mp3"))
    mime = "video/mp4" if is_video else "audio/mpeg"
    if target.is_file():
        return FileResponse(target, media_type=mime)
    ffmpeg = _find_ffmpeg()
    if not _conversion_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="The server is preparing other videos. Try again shortly or use VLC.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, suffix=target.suffix, delete=False) as temp:
            temp_path = pathlib.Path(temp.name)
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", file_path]
        if is_video:
            command += ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-movflags", "+faststart"]
        else:
            command += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-ac", "2"]
        command.append(str(temp_path))
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if result.returncode or not temp_path.stat().st_size:
            raise HTTPException(status_code=422, detail="This file could not be converted. It may be damaged or contain no playable media. Try VLC.")
        os.replace(temp_path, target)
        return FileResponse(target, media_type=mime)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preparing this file took too long. Try playing the original in VLC.")
    except OSError:
        raise HTTPException(status_code=503, detail="The server could not prepare playback. Check FFmpeg and free cache disk space.")
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        _conversion_slots.release()


def normalize_hls_quality(quality: Optional[str]) -> str:
    clean = (quality or "720p").strip().lower()
    if clean not in HLS_QUALITY_HEIGHTS:
        raise HTTPException(status_code=400, detail=f"Unsupported HLS quality '{quality}'. Choose from {', '.join(HLS_QUALITY_HEIGHTS)}.")
    return clean


def get_hls_cache_dir(file_path: str, quality: str = "720p") -> tuple[pathlib.Path, tuple]:
    import config
    clean_quality = normalize_hls_quality(quality)
    stat = _stat_media_file(file_path)
    key_raw = f"hls_v1:{file_path}:{stat.st_mtime_ns}:{stat.st_size}:{clean_quality}"
    key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    hls_dir = config.CACHE_DIR / "hls" / key
    job_key = (file_path, stat.st_mtime_ns, stat.st_size, clean_quality)
    return hls_dir, job_key


def start_hls_job(file_path: str, quality: str = "720p") -> dict:
    """Starts or resumes a segmented HLS transcode (m3u8 + .ts chunks) without blocking for whole-file completion."""
    clean_quality = normalize_hls_quality(quality)
    hls_dir, job_key = get_hls_cache_dir(file_path, clean_quality)
    playlist_path = hls_dir / "index.m3u8"
    complete_marker = hls_dir / ".complete"

    if complete_marker.is_file() and playlist_path.is_file():
        segs = len(list(hls_dir.glob("seg_*.ts")))
        return {"status": "ready", "complete": True, "segments_ready": segs, "quality": clean_quality, "dir": hls_dir}

    with _hls_lock:
        for old_key, job in list(_hls_jobs.items()):
            if job["status"] != "preparing" and time.monotonic() - job["updated"] > 3600:
                del _hls_jobs[old_key]
        existing = _hls_jobs.get(job_key)
        if existing and existing["status"] in ("preparing", "ready"):
            segs = len(list(hls_dir.glob("seg_*.ts"))) if hls_dir.is_dir() else 0
            status = "ready" if (playlist_path.is_file() and segs > 0) or complete_marker.is_file() else existing["status"]
            return {
                "status": status,
                "complete": complete_marker.is_file(),
                "segments_ready": segs,
                "quality": clean_quality,
                "dir": hls_dir,
            }
        if sum(1 for j in _hls_jobs.values() if j["status"] == "preparing") >= 2:
            raise HTTPException(status_code=503, detail="The server is transcoding other streams. Try again shortly.")
        ffmpeg = _find_ffmpeg()
        hls_dir.mkdir(parents=True, exist_ok=True)
        # Clean partial leftover files before a fresh run
        if not complete_marker.is_file():
            for old_file in hls_dir.iterdir():
                if old_file.is_file():
                    old_file.unlink(missing_ok=True)
        _hls_jobs[job_key] = {"status": "preparing", "updated": time.monotonic(), "detail": None}

    max_height = HLS_QUALITY_HEIGHTS[clean_quality]

    def worker():
        acquired = _hls_slots.acquire(timeout=30)
        if not acquired:
            with _hls_lock:
                _hls_jobs[job_key] = {"status": "failed", "updated": time.monotonic(), "detail": "Server transcode queue busy."}
            return
        try:
            vf = (
                f"scale=-2:'min(ih,{max_height})',pad=ceil(iw/2)*2:ceil(ih/2)*2"
                if max_height > 0
                else "pad=ceil(iw/2)*2:ceil(ih/2)*2"
            )
            cmd = [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", file_path,
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-f", "hls",
                "-hls_time", "4",
                "-hls_list_size", "0",
                "-hls_playlist_type", "event",
                "-hls_flags", "independent_segments+temp_file",
                "-hls_segment_filename", str(hls_dir / "seg_%05d.ts"),
                str(playlist_path),
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
            if result.returncode == 0 and playlist_path.is_file():
                complete_marker.write_text("ok", encoding="utf-8")
                with _hls_lock:
                    _hls_jobs[job_key] = {"status": "ready", "updated": time.monotonic(), "detail": None}
            else:
                with _hls_lock:
                    _hls_jobs[job_key] = {"status": "failed", "updated": time.monotonic(), "detail": "FFmpeg could not segment this video."}
        except subprocess.TimeoutExpired:
            with _hls_lock:
                _hls_jobs[job_key] = {"status": "failed", "updated": time.monotonic(), "detail": "HLS segmentation timed out."}
        except Exception:
            with _hls_lock:
                _hls_jobs[job_key] = {"status": "failed", "updated": time.monotonic(), "detail": "HLS segmentation failed."}
        finally:
            _hls_slots.release()

    threading.Thread(target=worker, daemon=True, name="HlsSegmenter").start()
    return {"status": "preparing", "complete": False, "segments_ready": 0, "quality": clean_quality, "dir": hls_dir}


def get_hls_playlist(file_path: str, quality: str = "720p", wait_seconds: float = 15.0) -> tuple[str, bool]:
    """Starts HLS segmentation if needed and returns (m3u8_text, is_complete) as soon as the first segment exists."""
    info = start_hls_job(file_path, quality)
    hls_dir: pathlib.Path = info["dir"]
    playlist_path = hls_dir / "index.m3u8"
    complete_marker = hls_dir / ".complete"

    if not info.get("complete"):
        deadline = time.monotonic() + wait_seconds
        _, job_key = get_hls_cache_dir(file_path, info["quality"])
        while time.monotonic() < deadline:
            with _hls_lock:
                job = _hls_jobs.get(job_key)
            if job and job["status"] == "failed":
                raise HTTPException(status_code=422, detail=job.get("detail") or "HLS transcoding failed.")
            if playlist_path.is_file() and (complete_marker.is_file() or any(hls_dir.glob("seg_*.ts"))):
                break
            time.sleep(0.25)

    if not playlist_path.is_file():
        raise HTTPException(
            status_code=503,
            detail="Preparing HLS stream segments. Retry shortly.",
            headers={"Retry-After": "2"},
        )
    return playlist_path.read_text(encoding="utf-8"), complete_marker.is_file()


def get_hls_status(file_path: str, quality: str = "720p") -> dict:
    clean_quality = normalize_hls_quality(quality)
    hls_dir, job_key = get_hls_cache_dir(file_path, clean_quality)
    playlist_path = hls_dir / "index.m3u8"
    complete_marker = hls_dir / ".complete"
    segs = len(list(hls_dir.glob("seg_*.ts"))) if hls_dir.is_dir() else 0
    if complete_marker.is_file() and playlist_path.is_file():
        return {"status": "ready", "complete": True, "segments_ready": segs, "quality": clean_quality}
    with _hls_lock:
        job = _hls_jobs.get(job_key)
    if job:
        status = "ready" if (job["status"] == "preparing" and playlist_path.is_file() and segs > 0) else job["status"]
        return {"status": status, "complete": False, "segments_ready": segs, "quality": clean_quality, "detail": job.get("detail")}
    return {"status": "idle", "complete": False, "segments_ready": segs, "quality": clean_quality}


def get_hls_segment_path(file_path: str, segment_name: str, quality: str = "720p") -> pathlib.Path:
    if not re.fullmatch(r"seg_\d{5}\.ts", segment_name):
        raise HTTPException(status_code=400, detail="Invalid HLS segment name.")
    hls_dir, _ = get_hls_cache_dir(file_path, quality)
    seg_path = hls_dir / segment_name
    if not seg_path.is_file():
        raise HTTPException(status_code=404, detail="HLS segment not ready yet.")
    return seg_path
