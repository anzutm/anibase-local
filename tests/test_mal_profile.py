"""Tests for MyAnimeList profile dashboard, animelist caching, local library bridge, and endpoints."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import main


class MalProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.orig_auth_file = main.MAL_AUTH_FILE
        self.orig_profile_cache = main.MAL_PROFILE_CACHE
        self.orig_cache_dir = main.CACHE_DIR
        self.orig_settings = main.SETTINGS_FILE

        self.test_auth_file = str(self.cache_dir / "mal_auth.json")
        self.test_profile_cache = str(self.cache_dir / "mal_profile_cache.json")
        self.test_settings_file = str(self.cache_dir / "settings.json")

        main.MAL_AUTH_FILE = self.test_auth_file
        main.MAL_PROFILE_CACHE = self.test_profile_cache
        main.CACHE_DIR = str(self.cache_dir)
        main.SETTINGS_FILE = self.test_settings_file

        main.save_settings({
            "setup_completed": True,
            "action_token": "test_action_token_12345",
            "library_folders": [str(self.root / "library")]
        })

        self.addCleanup(self.restore_globals)

        self.client = main.app.test_client()
        self.client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    def restore_globals(self):
        main.MAL_AUTH_FILE = self.orig_auth_file
        main.MAL_PROFILE_CACHE = self.orig_profile_cache
        main.CACHE_DIR = self.orig_cache_dir
        main.SETTINGS_FILE = self.orig_settings

    def test_context_processor_inject_mal_user(self):
        with main.app.test_request_context():
            res = main.inject_mal_user()
            self.assertFalse(res["mal_authenticated"])
            self.assertEqual(res["mal_username"], "")

            main.save_mal_auth({
                "access_token": "valid_token",
                "username": "TestMALUser",
                "picture": "https://cdn.myanimelist.net/test.jpg"
            })

            res2 = main.inject_mal_user()
            self.assertTrue(res2["mal_authenticated"])
            self.assertEqual(res2["mal_username"], "TestMALUser")
            self.assertEqual(res2["mal_picture"], "https://cdn.myanimelist.net/test.jpg")

    def test_profile_cache_crud(self):
        self.assertIsNone(main.load_mal_profile_cache())

        dummy = {
            "cached_at": time.time(),
            "user": {
                "id": 12345,
                "name": "Anzuuu_",
                "anime_statistics": {
                    "num_days_watched": 248.2,
                    "num_episodes": 16000
                }
            }
        }
        main.save_mal_profile_cache(dummy)
        loaded = main.load_mal_profile_cache()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["user"]["name"], "Anzuuu_")

        main.clear_mal_profile_cache()
        self.assertIsNone(main.load_mal_profile_cache())

    def test_animelist_cache_crud(self):
        status = "watching"
        self.assertIsNone(main.load_mal_animelist_cache(status))

        sample_items = [
            {"node": {"id": 1, "title": "Anime One"}, "list_status": {"score": 8, "num_episodes_watched": 5}}
        ]
        main.save_mal_animelist_cache(status, {
            "cached_at": time.time(),
            "items": sample_items
        })

        loaded = main.load_mal_animelist_cache(status)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded["items"]), 1)
        self.assertEqual(loaded["items"][0]["node"]["title"], "Anime One")

        main.clear_mal_profile_cache()
        self.assertIsNone(main.load_mal_animelist_cache(status))

    @patch("main.get_valid_mal_token")
    @patch("requests.get")
    def test_get_mal_user_full_profile_fetch_and_cache(self, mock_get, mock_token):
        mock_token.return_value = "token_abc"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 999,
            "name": "LiveProfileUser",
            "picture": "https://img.test/pic.png",
            "location": "Indonesia",
            "anime_statistics": {
                "num_days_watched": 150.5,
                "num_episodes": 10000,
                "mean_score": 7.5
            }
        }
        mock_get.return_value = mock_resp

        profile_data = main.get_mal_user_full_profile(force=True)
        self.assertFalse(profile_data.get("is_stale"))
        self.assertEqual(profile_data["user"]["name"], "LiveProfileUser")
        self.assertEqual(profile_data["user"]["anime_statistics"]["num_episodes"], 10000)

        # Subsequent call without force should hit cache without calling requests.get again
        mock_get.reset_mock()
        cached_result = main.get_mal_user_full_profile(force=False)
        self.assertEqual(cached_result["user"]["name"], "LiveProfileUser")
        mock_get.assert_not_called()

    @patch("main.get_anime")
    @patch("main.build_local_anime_match_index")
    def test_enrich_mal_list_with_local_library(self, mock_index_fn, mock_anime_fn):
        mock_anime_fn.return_value = [
            {"name": "Bocchi the Rock!", "folder": "Bocchi the Rock!"}
        ]
        mock_index_fn.return_value = {
            main.normalize_anime_match_name("Bocchi the Rock!"): {"name": "Bocchi the Rock!"}
        }

        mal_items = [
            {
                "node": {
                    "id": 47917,
                    "title": "Bocchi the Rock!",
                    "num_episodes": 12
                },
                "list_status": {
                    "score": 9,
                    "num_episodes_watched": 12,
                    "status": "completed"
                }
            },
            {
                "node": {
                    "id": 99999,
                    "title": "External Non-Existent Anime",
                    "num_episodes": 24
                },
                "list_status": {
                    "score": 0,
                    "num_episodes_watched": 1,
                    "status": "watching"
                }
            }
        ]

        with main.app.test_request_context():
            enriched = main.enrich_mal_list_with_local_library(mal_items)
            self.assertEqual(len(enriched), 2)

            # First item should match local library
            self.assertTrue(enriched[0]["in_library"])
            self.assertIsNotNone(enriched[0]["local_url"])
            self.assertIsNotNone(enriched[0]["play_url"])

            # Second item should be external
            self.assertFalse(enriched[1]["in_library"])
            self.assertIsNotNone(enriched[1]["external_url"])

    def test_mal_profile_page_unauthenticated(self):
        resp = self.client.get("/profile")
        self.assertEqual(resp.status_code, 200)
        content = resp.data.decode("utf-8")
        self.assertIn("Connect Your MyAnimeList Account", content)
        self.assertIn("/api/mal/login", content)

    @patch("main.get_mal_user_full_profile")
    @patch("main.get_mal_user_animelist")
    @patch("main.enrich_mal_list_with_local_library")
    def test_mal_profile_page_authenticated(self, mock_enrich, mock_animelist, mock_profile):
        main.save_mal_auth({
            "access_token": "user_token",
            "username": "HeroAnzu",
            "picture": ""
        })

        mock_profile.return_value = {
            "cached_at": time.time(),
            "user": {
                "name": "HeroAnzu",
                "joined_at": "2020-06-18",
                "location": "Indonesia",
                "anime_statistics": {
                    "num_items_watching": 4,
                    "num_items_completed": 100,
                    "num_items": 104,
                    "num_days_watched": 50.2,
                    "num_episodes": 2500,
                    "mean_score": 7.8
                }
            }
        }
        mock_animelist.return_value = []
        mock_enrich.return_value = []

        resp = self.client.get("/profile?status=watching")
        self.assertEqual(resp.status_code, 200)
        content = resp.data.decode("utf-8")
        self.assertIn("HeroAnzu", content)
        self.assertIn("Waktu Menonton", content)
        self.assertIn("50.2", content)
        self.assertIn("Currently Watching", content)

    def test_mal_refresh_profile_security(self):
        # Unauthenticated request
        resp = self.client.post("/api/mal/refresh-profile", headers={"X-Action-Token": main.get_action_token()})
        self.assertEqual(resp.status_code, 401)

        # Authenticated but missing action token
        main.save_mal_auth({"access_token": "valid_token", "username": "Tester"})
        resp2 = self.client.post("/api/mal/refresh-profile")
        self.assertEqual(resp2.status_code, 403)

    @patch("main.get_mal_user_full_profile")
    def test_mal_refresh_profile_success(self, mock_full_profile):
        main.save_mal_auth({"access_token": "valid_token", "username": "Tester"})
        mock_full_profile.return_value = {
            "cached_at": time.time(),
            "user": {"name": "Tester", "anime_statistics": {}}
        }

        resp = self.client.post(
            "/api/mal/refresh-profile",
            headers={"X-Action-Token": main.get_action_token()}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("user", {}).get("name"), "Tester")

    @patch("main.get_valid_mal_token")
    @patch("requests.patch")
    def test_mal_update_progress(self, mock_patch, mock_token):
        mock_token.return_value = "token_xyz"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"num_watched_episodes": 6, "status": "watching"}
        mock_patch.return_value = mock_resp

        main.save_mal_auth({"access_token": "token_xyz", "username": "Tester"})

        resp = self.client.post(
            "/api/mal/update-progress",
            headers={
                "X-Action-Token": main.get_action_token(),
                "Content-Type": "application/json"
            },
            data=json.dumps({
                "mal_id": 58749,
                "num_watched": 6,
                "status": "watching"
            })
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("result", {}).get("num_watched_episodes"), 6)


if __name__ == "__main__":
    unittest.main()
