import unittest
import tempfile
import os
import json
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
