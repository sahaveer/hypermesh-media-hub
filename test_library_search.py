import pathlib
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import config
import database
import main


class LibrarySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=config.DATA_DIR)
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_library_search.db"
        self.orig_db = config.DB_PATH
        config.DB_PATH = self.db_path
        database.init_db()

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.temp_dir.cleanup()

    def test_audio_and_video_api_query_filters_available_files(self):
        database.upsert_song({
            "id": "aud-1",
            "title": "Blinding Lights",
            "artist": "The Weeknd",
            "album": "After Hours",
            "duration": 200,
            "path": str(pathlib.Path(self.temp_dir.name) / "blinding.mp3"),
            "media_type": "audio",
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": "aud-2",
            "title": "Ocean Eyes",
            "artist": "Billie Eilish",
            "album": "Dont Smile at Me",
            "duration": 201,
            "path": str(pathlib.Path(self.temp_dir.name) / "ocean.mp3"),
            "media_type": "audio",
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": "vid-1",
            "title": "Interstellar Trailer",
            "artist": "Host",
            "album": "Movies",
            "duration": 150,
            "path": str(pathlib.Path(self.temp_dir.name) / "interstellar.mp4"),
            "media_type": "video",
            "resolution": "1080p",
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": "vid-2",
            "title": "Charlie Movie Clip",
            "artist": "Host",
            "album": "Movies",
            "duration": 180,
            "path": str(pathlib.Path(self.temp_dir.name) / "charlie.mp4"),
            "media_type": "video",
            "resolution": "720p",
            "owner_id": "host",
            "owner_name": "Host",
        })

        client = TestClient(main.app)

        audio_res = client.get("/api/songs", params={"query": "Weeknd"})
        self.assertEqual(audio_res.status_code, 200)
        audio_items = audio_res.json()["songs"]
        self.assertEqual([s["id"] for s in audio_items], ["aud-1"])

        video_res = client.get("/api/videos", params={"query": "Interstellar"})
        self.assertEqual(video_res.status_code, 200)
        video_items = video_res.json()["videos"]
        self.assertEqual([v["id"] for v in video_items], ["vid-1"])

    def test_webapp_has_dedicated_audio_and_video_search_bars(self):
        html = (config.BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="songs-search-input"', html)
        self.assertIn('id="songs-clear-search"', html)
        self.assertIn('id="videos-search-input"', html)
        self.assertIn('id="videos-clear-search"', html)


if __name__ == "__main__":
    unittest.main()
