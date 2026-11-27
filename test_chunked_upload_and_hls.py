import hashlib
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import imageio_ffmpeg

import config
import database
import main


def _make_test_png_bytes(color=(10, 180, 220)) -> bytes:
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    if len(data) < 600:
        # Pad PNG comment/tail safely? Actually resize 128x128 gradient so >512 bytes naturally
        img = Image.new("RGB", (256, 256), color=color)
        for x in range(256):
            img.putpixel((x, x % 256), (x, (x * 3) % 255, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
    return data


class ChunkedUploadAndHlsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmpdir.name)
        self.db_path = root / "test.db"
        self.photo_dir = root / "photos"
        self.music_dir = root / "music"
        self.video_dir = root / "videos"
        self.staging_dir = root / "staging"
        self.cache_dir = root / "cache"
        self.thumbs_dir = self.cache_dir / "photo_thumbs"
        self.video_thumbs_dir = self.cache_dir / "video_thumbs"
        self.covers_dir = self.cache_dir / "covers"
        for d in (
            self.photo_dir,
            self.music_dir,
            self.video_dir,
            self.staging_dir,
            self.thumbs_dir,
            self.video_thumbs_dir,
            self.covers_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        self.patches = [
            patch.object(config, "DB_PATH", self.db_path),
            patch.object(config, "DEFAULT_PHOTO_DIR", self.photo_dir),
            patch.object(config, "DEFAULT_MUSIC_DIR", self.music_dir),
            patch.object(config, "DEFAULT_VIDEO_DIR", self.video_dir),
            patch.object(config, "STAGING_DIR", self.staging_dir),
            patch.object(config, "CACHE_DIR", self.cache_dir),
            patch.object(config, "PHOTO_THUMBS_DIR", self.thumbs_dir),
            patch.object(config, "VIDEO_THUMBS_DIR", self.video_thumbs_dir),
            patch.object(config, "COVERS_DIR", self.covers_dir),
            patch("settings.is_upload_approval_required", return_value=False),
            patch("scanner.scan_library", return_value={"added": 0}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmpdir.cleanup()

    def test_photo_batch_deduplication_and_check_endpoint(self):
        png_bytes = _make_test_png_bytes((25, 140, 210))
        sha = hashlib.sha256(png_bytes).hexdigest()

        pre = self.client.post("/api/upload/photos/check", json={"hashes": [sha, "deadbeef"]})
        self.assertEqual(pre.status_code, 200)
        self.assertEqual(pre.json()["existing"], [])
        self.assertEqual(pre.json()["missing"], [sha, "deadbeef"])

        up1 = self.client.post(
            "/api/upload/photos",
            files=[("files", ("trip1.png", png_bytes, "image/png"))],
            data={"client_id": "phone1", "device_name": "Pixel"},
        )
        self.assertEqual(up1.status_code, 200)
        self.assertEqual(up1.json()["saved_count"], 1)
        self.assertEqual(up1.json()["skipped_count"], 0)

        post_check = self.client.post("/api/upload/photos/check", json={"hashes": [sha, "deadbeef"]})
        self.assertEqual(post_check.status_code, 200)
        self.assertEqual(post_check.json()["existing"], [sha])
        self.assertEqual(post_check.json()["missing"], ["deadbeef"])

        # Re-uploading identical bytes under a new filename must deduplicate
        up2 = self.client.post(
            "/api/upload/photos",
            files=[("files", ("trip1_copy.png", png_bytes, "image/png"))],
            data={"client_id": "phone1", "device_name": "Pixel"},
        )
        self.assertEqual(up2.status_code, 200)
        self.assertEqual(up2.json()["saved_count"], 0)
        self.assertEqual(up2.json()["skipped_count"], 1)

    def test_chunked_resumable_upload_and_dedup(self):
        png_bytes = _make_test_png_bytes((200, 90, 40))
        sha = hashlib.sha256(png_bytes).hexdigest()
        half = len(png_bytes) // 2

        init1 = self.client.post(
            "/api/upload/chunked/init",
            json={
                "filename": "big_shot.png",
                "total_size": len(png_bytes),
                "chunk_size": 256 * 1024,
                "content_hash": sha,
                "media_type": "photo",
                "client_id": "phone1",
                "device_name": "Pixel",
            },
        )
        self.assertEqual(init1.status_code, 200)
        self.assertEqual(init1.json()["status"], "created")
        upload_id = init1.json()["upload_id"]

        # Send first half
        p1 = self.client.put(f"/api/upload/chunked/{upload_id}?offset=0", content=png_bytes[:half])
        self.assertEqual(p1.status_code, 200)
        self.assertEqual(p1.json()["next_offset"], half)

        # Gap offset must be rejected with 409
        gap = self.client.put(f"/api/upload/chunked/{upload_id}?offset={half + 10}", content=b"123")
        self.assertEqual(gap.status_code, 409)

        # Re-calling init resumes at the exact offset
        resumed = self.client.post(
            "/api/upload/chunked/init",
            json={
                "filename": "big_shot.png",
                "total_size": len(png_bytes),
                "content_hash": sha,
                "media_type": "photo",
                "client_id": "phone1",
            },
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["status"], "resumed")
        self.assertEqual(resumed.json()["upload_id"], upload_id)
        self.assertEqual(resumed.json()["next_offset"], half)

        # Send second half & complete
        p2 = self.client.put(f"/api/upload/chunked/{upload_id}?offset={half}", content=png_bytes[half:])
        self.assertEqual(p2.status_code, 200)
        self.assertTrue(p2.json()["complete"])

        done = self.client.post(f"/api/upload/chunked/{upload_id}/complete")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "ok")

        # Subsequent init with same hash deduplicates immediately
        dup = self.client.post(
            "/api/upload/chunked/init",
            json={
                "filename": "big_shot_again.png",
                "total_size": len(png_bytes),
                "content_hash": sha,
                "media_type": "photo",
                "client_id": "phone1",
            },
        )
        self.assertEqual(dup.status_code, 200)
        self.assertTrue(dup.json()["deduplicated"])
        self.assertEqual(dup.json()["status"], "duplicate")

    def test_hls_api_endpoints(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        video = self.video_dir / "movie.mp4"
        subprocess.run(
            [
                ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=128x72:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2",
                "-c:v", "libx264", "-c:a", "aac", str(video),
            ],
            check=True,
        )
        song_id = "vid_hls_test"
        database.upsert_song({
            "id": song_id,
            "path": str(video.resolve()),
            "title": "HLS Test Movie",
            "artist": "Test",
            "album": "Test",
            "duration": 2.0,
            "filesize": video.stat().st_size,
            "mtime": video.stat().st_mtime,
            "mime_type": "video/mp4",
            "media_type": "video",
        })

        m3u8 = self.client.get(f"/api/hls/{song_id}/index.m3u8?quality=480p&t=abc")
        self.assertEqual(m3u8.status_code, 200)
        self.assertIn("#EXTM3U", m3u8.text)
        self.assertIn("seg_00000.ts?quality=480p&t=abc", m3u8.text)

        seg = self.client.get(f"/api/hls/{song_id}/seg_00000.ts?quality=480p")
        self.assertEqual(seg.status_code, 200)
        self.assertGreater(len(seg.content), 0)

        st = self.client.get(f"/api/hls/{song_id}/status?quality=480p")
        self.assertEqual(st.status_code, 200)
        self.assertIn(st.json()["status"], ("preparing", "ready"))


if __name__ == "__main__":
    unittest.main()
