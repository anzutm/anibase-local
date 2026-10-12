"""Tests for Post-Completion Quick Rating & Review (MAL & Local SQLite sync)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests

import main


class MalRateReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.orig_auth_file = main.MAL_AUTH_FILE
        self.test_auth_file = str(self.cache_dir / "mal_auth.json")
        main.MAL_AUTH_FILE = self.test_auth_file
        self.addCleanup(self.restore_globals)

        with main.db_connection() as conn:
            conn.execute("DELETE FROM anime_user_reviews")

    def restore_globals(self):
        main.MAL_AUTH_FILE = self.orig_auth_file

    def test_db_save_and_get_user_anime_review(self):
        review = main.get_user_anime_review("Bocchi the Rock!")
        self.assertIsNone(review)

        saved = main.save_user_anime_review("Bocchi the Rock!", mal_id=47917, score=10, comments="Masterpiece music anime!")
        self.assertTrue(saved)

        loaded = main.get_user_anime_review("Bocchi the Rock!")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["anime_name"], "Bocchi the Rock!")
        self.assertEqual(loaded["mal_id"], 47917)
        self.assertEqual(loaded["score"], 10)
        self.assertEqual(loaded["comments"], "Masterpiece music anime!")

        # Test update existing review
        main.save_user_anime_review("Bocchi the Rock!", mal_id=47917, score=9, comments="Updated verdict")
        loaded_updated = main.get_user_anime_review("Bocchi the Rock!")
        self.assertEqual(loaded_updated["score"], 9)
        self.assertEqual(loaded_updated["comments"], "Updated verdict")

    def test_api_mal_rate_review_local_when_not_authenticated(self):
        token = main.get_action_token()
        headers = {
            "X-AniBase-Action-Token": token,
            "Content-Type": "application/json"
        }

        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "get_cached_anilist_info", return_value={"mal_id": 52991}):
            client = main.app.test_client()
            resp = client.post(
                "/api/mal/rate-review",
                headers=headers,
                data=json.dumps({
                    "anime_name": "Sousou no Frieren Local",
                    "score": 10,
                    "comments": "Peak fantasy"
                })
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("ok"))
            self.assertTrue(data.get("local_only"))
            self.assertEqual(data.get("score"), 10)

            # Check DB persisted
            saved = main.get_user_anime_review("Sousou no Frieren Local")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["score"], 10)
            self.assertEqual(saved["comments"], "Peak fantasy")

    def test_api_mal_rate_review_authenticated_sync(self):
        main.save_mal_auth({"access_token": "valid_token", "username": "Otaku"})
        token = main.get_action_token()
        headers = {
            "X-AniBase-Action-Token": token,
            "Content-Type": "application/json"
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "completed",
            "score": 9,
            "comments": "Awesome show"
        }

        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "is_mal_authenticated", return_value=True), \
             patch.object(main, "get_valid_mal_token", return_value="valid_token"), \
             patch.object(main, "get_cached_anilist_info", return_value={"mal_id": 52991}), \
             patch.object(main.requests, "patch", return_value=mock_resp):
            client = main.app.test_client()
            resp = client.post(
                "/api/mal/rate-review",
                headers=headers,
                data=json.dumps({
                    "anime_name": "Sousou no Frieren",
                    "mal_id": 52991,
                    "score": 9,
                    "comments": "Awesome show",
                    "status": "completed"
                })
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("score"), 9)
            self.assertEqual(data.get("comments"), "Awesome show")

            saved = main.get_user_anime_review("Sousou no Frieren")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["score"], 9)
            self.assertEqual(saved["comments"], "Awesome show")

    def test_api_watch_status_progress_completion_flags(self):
        token = main.get_action_token()
        headers = {
            "X-AniBase-Action-Token": token,
            "Content-Type": "application/json"
        }

        # Case 1: Final episode completed of a finished show
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "get_cached_anilist_info", return_value={"status": "FINISHED", "episodes": 12, "mal_id": 12345}), \
             patch.object(main, "get_episode_number", return_value=12):
            client = main.app.test_client()
            resp = client.post(
                "/api/watch-status/progress",
                headers=headers,
                data=json.dumps({
                    "anime_name": "Test Anime Completed",
                    "episode": "Test Anime - 12.mkv",
                    "progress": 95,
                    "duration": 1400,
                    "current_seconds": 1330
                })
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("ok"))
            self.assertTrue(data.get("is_completed"))
            self.assertEqual(data.get("mal_id"), 12345)
            self.assertEqual(data.get("ep_num"), 12)
            self.assertEqual(data.get("total_episodes"), 12)

        # Case 2: Intermediate episode should NOT be completed
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "get_cached_anilist_info", return_value={"status": "FINISHED", "episodes": 12, "mal_id": 12345}), \
             patch.object(main, "get_episode_number", return_value=5):
            client = main.app.test_client()
            resp = client.post(
                "/api/watch-status/progress",
                headers=headers,
                data=json.dumps({
                    "anime_name": "Test Anime In Progress",
                    "episode": "Test Anime - 05.mkv",
                    "progress": 90,
                    "duration": 1400,
                    "current_seconds": 1260
                })
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("ok"))
            self.assertFalse(data.get("is_completed"))


if __name__ == "__main__":
    unittest.main()
