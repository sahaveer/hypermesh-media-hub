import gc
import io
import math
import pathlib
import struct
import tempfile
import unittest
import wave
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
import database
import main
import scanner


def _write_wav(path: pathlib.Path, duration_secs: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        for i in range(44100 * duration_secs):
            val = int(32767.0 * 0.25 * math.sin(2.0 * math.pi * 440.0 * (i / 44100)))
            wav_file.writeframesraw(struct.pack("<h", val))


def _write_dummy_mp4(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 1024)


class DeleteMediaCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=config.DATA_DIR, ignore_cleanup_errors=True)
        self.addCleanup(self._cleanup_temp)
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = self.root / "test_media.db"

        self.db_patch = patch("database.DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

        database.init_db()

        self.host_song_path = self.root / "host_song.wav"
        self.host_song_2_path = self.root / "host_song_2.wav"
        self.host_video_path = self.root / "host_video.mp4"
        self.peer_song_path = self.root / "peer_song.wav"

        _write_wav(self.host_song_path)
        _write_wav(self.host_song_2_path)
        _write_dummy_mp4(self.host_video_path)
        _write_wav(self.peer_song_path)

        self.host_song_id = "host-song-1"
        self.host_song_2_id = "host-song-2"
        self.host_video_id = "host-video-1"
        self.peer_song_id = "peer-song-1"

        database.upsert_song({
            "id": self.host_song_id,
            "path": str(self.host_song_path),
            "title": "Host Song One",
            "artist": "Host Artist",
            "media_type": "audio",
            "duration": 1.0,
            "filesize": self.host_song_path.stat().st_size,
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": self.host_song_2_id,
            "path": str(self.host_song_2_path),
            "title": "Host Song Two",
            "artist": "Host Artist",
            "media_type": "audio",
            "duration": 1.0,
            "filesize": self.host_song_2_path.stat().st_size,
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": self.host_video_id,
            "path": str(self.host_video_path),
            "title": "Host Video One",
            "artist": "Video / Movie",
            "media_type": "video",
            "duration": 5.0,
            "filesize": self.host_video_path.stat().st_size,
            "owner_id": "host",
            "owner_name": "Host",
        })
        database.upsert_song({
            "id": self.peer_song_id,
            "path": str(self.peer_song_path),
            "title": "Peer Uploaded Song",
            "artist": "Peer Artist",
            "media_type": "audio",
            "duration": 1.0,
            "filesize": self.peer_song_path.stat().st_size,
            "owner_id": "peer_alice",
            "owner_name": "Alice Phone",
        })

        self.local_client = TestClient(main.app)
        self.remote_client = TestClient(main.app, client=("8.8.8.8", 54321))

    def _cleanup_temp(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_can_delete_flags_for_admin_browser_and_peers(self):
        # Host browser sends a generated X-Client-Id (client_abc123); must see can_delete=True on host & peer media
        songs_res = self.local_client.get("/api/songs", headers={"X-Client-Id": "client_abc123"})
        self.assertEqual(songs_res.status_code, 200)
        songs_by_id = {s["id"]: s for s in songs_res.json()["songs"]}
        self.assertTrue(songs_by_id[self.host_song_id]["can_delete"])
        self.assertFalse(songs_by_id[self.host_song_id]["is_owner"])
        self.assertTrue(songs_by_id[self.peer_song_id]["can_delete"])
        self.assertFalse(songs_by_id[self.peer_song_id]["is_owner"])

        videos_res = self.local_client.get("/api/videos", headers={"X-Client-Id": "client_abc123"})
        self.assertEqual(videos_res.status_code, 200)
        self.assertTrue(videos_res.json()["videos"][0]["can_delete"])

        # Remote peer Alice sees can_delete=True only on her own upload, False on host media
        alice_res = self.remote_client.get("/api/songs", headers={"X-Client-Id": "peer_alice"})
        self.assertEqual(alice_res.status_code, 200)
        alice_by_id = {s["id"]: s for s in alice_res.json()["songs"]}
        self.assertFalse(alice_by_id[self.host_song_id]["can_delete"])
        self.assertTrue(alice_by_id[self.peer_song_id]["can_delete"])
        self.assertTrue(alice_by_id[self.peer_song_id]["is_owner"])

        # Playlist tracks also include can_delete
        pl_id = database.create_playlist("Test Delete PL")
        database.add_songs_to_playlist(pl_id, [self.host_song_id])
        pl_res = self.local_client.get(f"/api/playlists/{pl_id}", headers={"X-Client-Id": "client_abc123"})
        self.assertEqual(pl_res.status_code, 200)
        self.assertTrue(pl_res.json()["songs"][0]["can_delete"])

    def test_delete_single_song_and_video_removes_file_and_db_record(self):
        # Remote stranger cannot delete host video
        forbidden = self.remote_client.delete(
            f"/api/media/{self.host_video_id}",
            headers={"X-Client-Id": "peer_bob"}
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertTrue(self.host_video_path.exists())

        # Host browser (with client_id header) deletes host video
        res_vid = self.local_client.delete(
            f"/api/media/{self.host_video_id}",
            headers={"X-Client-Id": "client_abc123"}
        )
        self.assertEqual(res_vid.status_code, 200)
        self.assertFalse(self.host_video_path.exists())
        self.assertIsNone(database.get_song(self.host_video_id))

        # Owner peer deletes own song
        res_peer = self.remote_client.delete(
            f"/api/media/{self.peer_song_id}",
            headers={"X-Client-Id": "peer_alice"}
        )
        self.assertEqual(res_peer.status_code, 200)
        self.assertFalse(self.peer_song_path.exists())
        self.assertIsNone(database.get_song(self.peer_song_id))

    def test_batch_delete_media_permissions_and_cleanup(self):
        # Remote stranger trying to batch delete host songs gets 403
        forbidden = self.remote_client.post(
            "/api/media/batch-delete",
            json={"media_ids": [self.host_song_id, self.host_song_2_id]},
            headers={"X-Client-Id": "peer_bob"}
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertTrue(self.host_song_path.exists())

        # Host admin batch deletes multiple songs
        res = self.local_client.post(
            "/api/media/batch-delete",
            json={"media_ids": [self.host_song_id, self.host_song_2_id]},
            headers={"X-Client-Id": "client_abc123"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["deleted_count"], 2)
        self.assertFalse(self.host_song_path.exists())
        self.assertFalse(self.host_song_2_path.exists())
        self.assertIsNone(database.get_song(self.host_song_id))
        self.assertIsNone(database.get_song(self.host_song_2_id))

    def test_live_peer_share_delete(self):
        fake_audio = io.BytesIO(b"RIFF....WAVEfmt " + b"\x00" * 512)
        res = self.remote_client.post(
            "/api/peers/share",
            data={"client_id": "peer_alice", "device_name": "Alice Phone"},
            files={"file": ("ephemeral_song.wav", fake_audio, "audio/wav")},
            headers={"X-Client-Id": "peer_alice"},
        )
        self.assertEqual(res.status_code, 200)
        share_id = res.json()["share"]["id"]
        temp_path = pathlib.Path(res.json()["share"]["file_path"])
        self.assertTrue(temp_path.exists())

        # Stranger cannot delete
        bad = self.remote_client.delete(
            f"/api/peers/shares/{share_id}",
            headers={"X-Client-Id": "peer_bob"},
        )
        self.assertEqual(bad.status_code, 403)
        self.assertTrue(temp_path.exists())

        # Owner can delete
        ok = self.remote_client.delete(
            f"/api/peers/shares/{share_id}",
            headers={"X-Client-Id": "peer_alice"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertFalse(temp_path.exists())

    def test_frontend_ui_contains_delete_and_confirmation_elements(self):
        html_res = self.local_client.get("/")
        self.assertEqual(html_res.status_code, 200)
        for element_id in (
            "confirm-delete-modal",
            "confirm-modal-confirm",
            "confirm-modal-cancel",
            "video-modal-delete-btn",
            "full-delete-btn",
            "batch-delete-btn",
            "app-toast",
        ):
            self.assertIn(element_id, html_res.text)

        js_res = self.local_client.get("/app.js")
        self.assertEqual(js_res.status_code, 200)
        for symbol in ("showConfirmModal", "canDeleteMedia", "batchDeleteSelectedMedia", "/api/media/batch-delete"):
            self.assertIn(symbol, js_res.text)


if __name__ == "__main__":
    unittest.main()
