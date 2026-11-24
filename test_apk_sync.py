import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
import main


class FakeResponse:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ApkSyncTests(unittest.TestCase):
    def test_load_dotenv_file_sets_missing_env_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / ".env"
            env_file.write_text('# comment\nAPK_GITHUB_TOKEN="ghp_from_dotenv"\nexport TEST_FLAG=yes\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("APK_GITHUB_TOKEN", None)
                os.environ.pop("TEST_FLAG", None)
                config._load_dotenv_file(env_file)
                self.assertEqual(os.environ.get("APK_GITHUB_TOKEN"), "ghp_from_dotenv")
                self.assertEqual(os.environ.get("TEST_FLAG"), "yes")

    def test_parse_release_version(self):
        self.assertEqual(main._parse_release_version("v1.2.3+14"), ("1.2.3", 14))
        self.assertEqual(main._parse_release_version("2.0.0"), ("2.0.0", 1))
        self.assertEqual(main._parse_release_version(""), ("1.0.0", 1))

    def test_public_settings_hides_github_token(self):
        cleaned = main.public_settings({
            "admin_token": "secret-admin",
            "global_tunnel_token": "secret-tunnel",
            "apk_github_token": "ghp_secret_token",
            "apk_github_repo": "sahaveer/hypermesh-mobile-app",
        })
        self.assertNotIn("apk_github_token", cleaned)
        self.assertNotIn("global_tunnel_token", cleaned)
        self.assertNotIn("admin_token", cleaned)
        self.assertTrue(cleaned["apk_github_token_set"])
        self.assertTrue(cleaned["admin_password_set"])

    def test_sync_github_and_serve_apk_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            apk_info = tmp_dir / "version.json"
            fake_release = {
                "tag_name": "v1.0.5+9",
                "body": "Test release notes",
                "assets": [
                    {
                        "name": "hypermeshmediaplayer.apk",
                        "url": "https://api.github.com/repos/sahaveer/hypermesh-mobile-app/releases/assets/999",
                    }
                ],
            }
            fake_apk_bytes = b"PK\x03\x04fake-apk-payload"

            def fake_urlopen(req, timeout=20):
                url = req.full_url
                if url.endswith("/releases/latest"):
                    return FakeResponse(json.dumps(fake_release).encode("utf-8"))
                if url.endswith("/releases/assets/999"):
                    return FakeResponse(fake_apk_bytes)
                raise AssertionError(f"Unexpected URL: {url}")

            with (
                patch.object(main, "APK_DIR", tmp_dir),
                patch.object(main, "APK_INFO_FILE", apk_info),
                patch("main.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("main.is_host_admin", return_value=True),
            ):
                client = TestClient(main.app)
                res = client.post(
                    "/api/app/sync-github",
                    json={"repo": "sahaveer/hypermesh-mobile-app", "token": "ghp_test"},
                )
                self.assertEqual(res.status_code, 200, res.text)
                payload = res.json()
                self.assertEqual(payload["status"], "synced")
                self.assertEqual(payload["version"], "1.0.5")
                self.assertEqual(payload["build_number"], 9)
                self.assertEqual(payload["apk_url"], "/hypermeshmediaplayer.apk")

                ver = client.get("/api/app/version")
                self.assertEqual(ver.status_code, 200)
                self.assertTrue(ver.json()["apk_available"])
                self.assertEqual(ver.json()["build_number"], 9)

                for route in ("/hypermeshmediaplayer.apk", "/api/app/latest.apk"):
                    dl = client.get(route)
                    self.assertEqual(dl.status_code, 200)
                    self.assertEqual(dl.content, fake_apk_bytes)
                    self.assertIn("hypermeshmediaplayer.apk", dl.headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
