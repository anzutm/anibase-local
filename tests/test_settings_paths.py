import unittest
import tempfile
import os
import json
from unittest.mock import patch, MagicMock

from anibase.web import create_app
from anibase.web.routes_api import inspect_folder_path, analyze_path_hierarchy


class TestSettingsLibraryPaths(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_inspect_folder_path_empty_and_not_found(self):
        res_empty = inspect_folder_path("")
        self.assertEqual(res_empty["status"], "empty")

        res_missing = inspect_folder_path(os.path.join(self.tmp_dir.name, "non_existent"))
        self.assertEqual(res_missing["status"], "not_found")
        self.assertFalse(res_missing["exists"])

    def test_inspect_folder_path_valid(self):
        anime_dir = os.path.join(self.tmp_dir.name, "AnimeFolder")
        os.makedirs(os.path.join(anime_dir, "Sousou no Frieren"))
        os.makedirs(os.path.join(anime_dir, "One Piece"))

        res = inspect_folder_path(anime_dir, is_movie=False)
        self.assertEqual(res["status"], "valid")
        self.assertTrue(res["exists"])
        self.assertTrue(res["is_dir"])
        self.assertEqual(res["subfolders"], 2)
        self.assertIn("2 folder anime", res["message"])
        self.assertIn("free", res["message"])

    def test_analyze_path_hierarchy(self):
        parent = os.path.join(self.tmp_dir.name, "Anime")
        nested = os.path.join(parent, "Ongoing")
        other = os.path.join(self.tmp_dir.name, "Other")
        os.makedirs(parent, exist_ok=True)
        os.makedirs(nested, exist_ok=True)
        os.makedirs(other, exist_ok=True)

        paths = [parent, nested, other, parent]
        hierarchy = analyze_path_hierarchy(paths)

        # Index 1 should be nested under parent
        self.assertIn(1, hierarchy)
        self.assertTrue(hierarchy[1].get("is_nested"))

        # Index 3 is a duplicate of index 0
        self.assertIn(3, hierarchy)
        self.assertTrue(hierarchy[3].get("is_duplicate"))

    def test_api_settings_validate_paths_endpoint(self):
        parent = os.path.join(self.tmp_dir.name, "AnimeLib")
        os.makedirs(os.path.join(parent, "Anime_1"), exist_ok=True)
        movie_dir = os.path.join(self.tmp_dir.name, "Movies")
        os.makedirs(movie_dir, exist_ok=True)

        res = self.client.post("/api/settings/validate-paths", json={
            "library_paths": [parent],
            "movie_path": movie_dir
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["library_results"]), 1)
        self.assertEqual(data["library_results"][0]["status"], "valid")
        self.assertEqual(data["movie_result"]["status"], "valid")


    def test_update_settings_preserves_unsubmitted_keys(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        with patch("anibase.web.routes_api.load_settings") as mock_load, \
             patch("anibase.web.routes_api.save_settings") as mock_save, \
             patch("anibase.web.routes_api.apply_settings"), \
             patch("anibase.web.routes_api.reconfigure_library_observer"), \
             patch("anibase.web.routes_api.start_auto_import_worker"), \
             patch("anibase.web.routes_api.sync_all_library"):

            mock_load.return_value = {
                "lendrive_cookie": "cf_clearance=test_token_123",
                "lendrive_base_url": "https://lendrive.web.id",
                "download_max_concurrent": 3,
                "ongoing_tracker_enabled": True,
                "action_token": token
            }

            res = self.client.post("/settings", data={
                "action_token": token,
                "library_paths": ["D:/Anime/Lib"],
                "movie_path": "D:/Movies",
                "theme_preset": "dark-blue"
            }, headers={"X-Action-Token": token})

            self.assertEqual(res.status_code, 302)
            saved_dict = mock_save.call_args[0][0]
            # Must preserve lendrive and download settings!
            self.assertEqual(saved_dict.get("lendrive_cookie"), "cf_clearance=test_token_123")
            self.assertEqual(saved_dict.get("lendrive_base_url"), "https://lendrive.web.id")
            self.assertEqual(saved_dict.get("download_max_concurrent"), 3)
            self.assertTrue(saved_dict.get("ongoing_tracker_enabled"))


    def test_api_settings_detect_players_endpoint(self):
        res = self.client.get("/api/settings/detect-players")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["players"], list)

    def test_api_settings_lan_info_and_qr(self):
        res = self.client.get("/api/settings/lan-info")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("lan_ip", data)
        self.assertIn("url", data)
        self.assertEqual(data["port"], 5000)
        self.assertIn("qr_svg", data)
        self.assertIn("<svg", data["qr_svg"])

        res_qr = self.client.get("/api/settings/lan-qr")
        self.assertEqual(res_qr.status_code, 200)
        self.assertIn("image/svg+xml", res_qr.content_type)
        self.assertIn(b"<svg", res_qr.data)

    def test_api_settings_test_discord_endpoint(self):
        with patch("anibase.web.routes_api.load_settings") as mock_load:
            mock_load.return_value = {"discord_client_id": ""}
            # Empty client ID
            res = self.client.post("/api/settings/test-discord", json={"client_id": ""})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertFalse(data["ok"])
            self.assertFalse(data["connected"])
            self.assertEqual(data["status"], "missing_id")

        # Invalid numeric client ID
        res = self.client.post("/api/settings/test-discord", json={"client_id": "abc"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "invalid_id")

        # Mock successful connection
        with patch("anibase.constants.Presence") as mock_presence:
            mock_inst = MagicMock()
            mock_presence.return_value = mock_inst
            res_ok = self.client.post("/api/settings/test-discord", json={"client_id": "123456789012345678"})
            self.assertEqual(res_ok.status_code, 200)
            data_ok = res_ok.get_json()
            self.assertTrue(data_ok["ok"])
            self.assertTrue(data_ok["connected"])
            self.assertEqual(data_ok["status"], "connected")

    @patch("requests.get")
    def test_api_settings_test_lendrive_endpoint(self, mock_get):
        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><h1>Lendrive Anime</h1></body></html>"

        res = self.client.post("/api/settings/test-lendrive", json={
            "base_url": "https://lendrive.web.id",
            "cookie": "wssplashchk=valid_test_cookie"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["valid"])
        self.assertFalse(data["challenge"])

        # Test challenge page detection
        mock_resp.status_code = 403
        mock_resp.text = "<html><title>Just a moment...</title><body>Checking your browser</body></html>"
        res2 = self.client.post("/api/settings/test-lendrive", json={
            "base_url": "https://lendrive.web.id",
            "cookie": ""
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2["ok"])
        self.assertFalse(data2["valid"])
        self.assertTrue(data2["challenge"])

    def test_update_settings_persists_downloader_form_fields(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        with patch("anibase.web.routes_api.load_settings") as mock_load, \
             patch("anibase.web.routes_api.save_settings") as mock_save, \
             patch("anibase.web.routes_api.apply_settings"), \
             patch("anibase.web.routes_api.reconfigure_library_observer"), \
             patch("anibase.web.routes_api.start_auto_import_worker"), \
             patch("anibase.web.routes_api.sync_all_library"):

            mock_load.return_value = {
                "action_token": token,
                "download_max_concurrent": 2,
                "lendrive_base_url": "https://lendrive.web.id",
                "lendrive_cookie": ""
            }

            res = self.client.post("/settings", data={
                "action_token": token,
                "downloader_settings_present": "1",
                "download_max_concurrent": "4",
                "lendrive_base_url": "https://mirror.lendrive.cc",
                "lendrive_cookie": "cf_clearance=new_secret_123",
                "ongoing_tracker_enabled": "on",
                "ongoing_tracker_interval_minutes": "60",
                "ongoing_tracker_notify_toast": "on"
            }, headers={"X-Action-Token": token})

            self.assertEqual(res.status_code, 302)
            saved_dict = mock_save.call_args[0][0]
            self.assertEqual(saved_dict.get("download_max_concurrent"), 4)
            self.assertEqual(saved_dict.get("lendrive_base_url"), "https://mirror.lendrive.cc")
            self.assertEqual(saved_dict.get("lendrive_cookie"), "cf_clearance=new_secret_123")
            self.assertTrue(saved_dict.get("ongoing_tracker_enabled"))
            self.assertEqual(saved_dict.get("ongoing_tracker_interval_minutes"), 60)
            self.assertTrue(saved_dict.get("ongoing_tracker_notify_toast"))


if __name__ == "__main__":
    unittest.main()
