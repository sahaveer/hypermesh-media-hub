import os
import pathlib

import os
import sys
import pathlib

# Support running either as standard Python script or compiled PyInstaller .exe
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BASE_DIR = pathlib.Path(sys._MEIPASS)
    # When running as an .exe, store data next to the executable
    APP_DIR = pathlib.Path(sys.executable).resolve().parent
else:
    BASE_DIR = pathlib.Path(__file__).resolve().parent
    APP_DIR = BASE_DIR

def _load_dotenv_file(env_path: pathlib.Path) -> None:
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
    except Exception:
        pass

_load_dotenv_file(APP_DIR / ".env")

# Default Music directory
DEFAULT_MUSIC_DIR = pathlib.Path(os.path.expanduser("~/Music"))
if not DEFAULT_MUSIC_DIR.exists():
    try:
        DEFAULT_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# Default Videos directory
DEFAULT_VIDEO_DIR = pathlib.Path(os.path.expanduser("~/Videos"))
if not DEFAULT_VIDEO_DIR.exists():
    try:
        DEFAULT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# Default Photos directory
DEFAULT_PHOTO_DIR = pathlib.Path(os.path.expanduser("~/Pictures"))
if not DEFAULT_PHOTO_DIR.exists():
    try:
        DEFAULT_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# Supported Audio & Video Formats
AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".m4v",
    ".ts",
    ".mpg",
    ".mpeg",
    ".vob",
}

PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".gif",
    ".bmp",
    ".tiff",
}

# Ambiguous formats that can be audio or video
DUAL_EXTENSIONS = {
    ".webm",
}

SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS.union(VIDEO_EXTENSIONS).union(DUAL_EXTENSIONS).union(PHOTO_EXTENSIONS)

# Cache & DB Paths
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

STAGING_DIR = DATA_DIR / "staging"
STAGING_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = DATA_DIR / "cache"
COVERS_DIR = CACHE_DIR / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_THUMBS_DIR = CACHE_DIR / "video_thumbs"
VIDEO_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

PHOTO_THUMBS_DIR = CACHE_DIR / "photo_thumbs"
PHOTO_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

STATUS_DIR = DATA_DIR / "statuses"
STATUS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "media.db"

# Server Host & Port
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8000))
