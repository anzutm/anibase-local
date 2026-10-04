"""Tests for MyAnimeList auto-scrobble, OAuth PKCE authentication, and status updates."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import main


class MalScrobbleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.orig_auth_file = main.MAL_AUTH_FILE
        self.orig_scrobble_file = main.MAL_SCROBBLE_CACHE
        self.test_auth_file = str(self.cache_dir / "mal_auth.json")
        self.test_scrobble_file = str(self.cache_dir / "mal_scrobble_cache.json")

        main.MAL_AUTH_FILE = self.test_auth_file
        main.MAL_SCROBBLE_CACHE = self.test_scrobble_file
        self.addCleanup(self.restore_globals)

        self.video = self.root / "Episode 05.mkv"
        self.video.write_bytes(b"video content")

    def restore_globals(self):
        main.MAL_AUTH_FILE = self.orig_auth_file
        main.MAL_SCROBBLE_CACHE = self.orig_scrobble_file

    def test_auth_persistence_and_status(self):
        self.assertFalse(main.is_mal_authenticated())
        self.assertEqual(main.load_mal_auth(), {})

        auth_data = {
            "access_token": "test_token_123",
            "refresh_token": "test_refresh_456",
            "expires_at": time.time() + 3600,
            "username": "AnimeFan"
        }
        main.save_mal_auth(auth_data)

        self.assertTrue(main.is_mal_authenticated())
        loaded = main.load_mal_auth()
        self.assertEqual(loaded.get("username"), "AnimeFan")
        self.assertEqual(loaded.get("access_token"), "test_token_123")

        main.clear_mal_auth()
        self.assertFalse(main.is_mal_authenticated())
        self.assertEqual(main.load_mal_auth(), {})

    def test_get_valid_mal_token_active(self):
        auth_data = {
            "access_token": "valid_token",
            "refresh_token": "refresh_token",
            "expires_at": time.time() + 1800,
            "username": "AnimeFan"
        }
        main.save_mal_auth(auth_data)
        self.assertEqual(main.get_valid_mal_token(), "valid_token")

    def test_get_valid_mal_token_refreshes_when_expired(self):
        auth_data = {
            "access_token": "old_token",
            "refresh_token": "refresh_abc",
            "expires_at": time.time() - 100,  # Expired
            "username": "AnimeFan"
        }
        main.save_mal_auth(auth_data)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new_refreshed_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600
        }

        with patch.object(main, "load_settings", return_value={"mal_client_id": "mal_client_123"}), \
             patch.object(main.requests, "post", return_value=mock_resp) as mock_post:
            token = main.get_valid_mal_token()
            self.assertEqual(token, "new_refreshed_token")
            mock_post.assert_called_once()
            loaded = main.load_mal_auth()
            self.assertEqual(loaded.get("access_token"), "new_refreshed_token")

    def test_scrobble_disabled_returns_not_enabled(self):
        with patch.object(main, "load_settings", return_value={"mal_scrobble_enabled": False}):
            res = main.scrobble_episode_to_mal("Sousou no Frieren", "Episode 01.mkv")
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason"], "not_enabled")

    def test_scrobble_success_and_deduplication(self):
        auth_data = {
            "access_token": "test_token",
            "expires_at": time.time() + 3600,
            "username": "AnimeFan"
        }
        main.save_mal_auth(auth_data)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        settings = {
            "mal_scrobble_enabled": True,
            "mal_client_id": "client_1",
            "mal_scrobble_threshold": 90
        }
        anilist_info = {
            "mal_id": 52991,
            "episodes": 28
        }

        with patch.object(main, "load_settings", return_value=settings), \
             patch.object(main, "get_cached_anilist_info", return_value=anilist_info), \
             patch.object(main.requests, "patch", return_value=mock_resp) as mock_patch:
            # 1. First scrobble must succeed and call MAL API with watching status
            res = main.scrobble_episode_to_mal("Sousou no Frieren", "Sousou no Frieren - 05.mkv")
            self.assertTrue(res["ok"])
            self.assertEqual(res["ep_num"], 5)
            self.assertEqual(res["status"], "watching")
            self.assertEqual(res["mal_id"], 52991)
            mock_patch.assert_called_once()
            call_kwargs = mock_patch.call_args
            self.assertEqual(call_kwargs[1]["data"]["num_watched_episodes"], 5)
            self.assertEqual(call_kwargs[1]["data"]["status"], "watching")

            # 2. Second scrobble for the same episode must be deduplicated
            mock_patch.reset_mock()
            res2 = main.scrobble_episode_to_mal("Sousou no Frieren", "Sousou no Frieren - 05.mkv")
            self.assertTrue(res2["ok"])
            self.assertTrue(res2.get("already_scrobbled"))
            mock_patch.assert_not_called()

            # 3. Unwatching clears the record
            main.clear_mal_scrobble_record("Sousou no Frieren", "Sousou no Frieren - 05.mkv")
            # Now it can scrobble again
            res3 = main.scrobble_episode_to_mal("Sousou no Frieren", "Sousou no Frieren - 05.mkv")
            self.assertTrue(res3["ok"])
            self.assertFalse(res3.get("already_scrobbled", False))
            mock_patch.assert_called_once()

    def test_scrobble_sets_completed_on_final_episode(self):
        auth_data = {
            "access_token": "test_token",
            "expires_at": time.time() + 3600
        }
        main.save_mal_auth(auth_data)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        settings = {"mal_scrobble_enabled": True}
        anilist_info = {"mal_id": 12345, "episodes": 12}

        with patch.object(main, "load_settings", return_value=settings), \
             patch.object(main, "get_cached_anilist_info", return_value=anilist_info), \
             patch.object(main.requests, "patch", return_value=mock_resp) as mock_patch:
            res = main.scrobble_episode_to_mal("Test Anime", "Test Anime - 12.mkv")
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "completed")
            call_kwargs = mock_patch.call_args
            self.assertEqual(call_kwargs[1]["data"]["status"], "completed")

    def test_oauth_login_endpoint_redirects_with_pkce(self):
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "load_settings", return_value={"mal_client_id": "test_client_id_999"}):
            client = main.app.test_client()
            resp = client.get("/api/mal/login")
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("myanimelist.net/v1/oauth2/authorize", location)
            self.assertIn("client_id=test_client_id_999", location)
            self.assertIn("code_challenge=", location)
            self.assertIn("code_challenge_method=plain", location)
            self.assertIn("response_type=code", location)

    def test_oauth_login_post_saves_client_id_and_redirects(self):
        settings_store = {}
        def mock_save(s):
            settings_store.update(s)
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "load_settings", side_effect=lambda: dict(settings_store)), \
             patch.object(main, "save_settings", side_effect=mock_save):
            client = main.app.test_client()
            resp = client.post("/api/mal/login", data={"mal_client_id": "fresh_posted_client_id"})
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("client_id=fresh_posted_client_id", location)
            self.assertEqual(settings_store.get("mal_client_id"), "fresh_posted_client_id")

    def test_oauth_login_missing_client_id_redirects_with_error(self):
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "load_settings", return_value={}):
            client = main.app.test_client()
            resp = client.get("/api/mal/login")
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/settings?mal_error=", resp.headers.get("Location", ""))

    def test_oauth_callback_endpoint_exchanges_token(self):
        main.MAL_OAUTH_SESSIONS["state_123"] = {
            "verifier": "verifier_abc",
            "timestamp": time.time()
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600
        }

        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "load_settings", return_value={"mal_client_id": "my_client_id", "mal_scrobble_enabled": False}), \
             patch.object(main, "save_settings") as mock_save_settings, \
             patch.object(main, "fetch_mal_user_profile", return_value={"id": 1, "name": "OtakuUser", "picture": "http://img.jpg"}), \
             patch.object(main.requests, "post", return_value=mock_resp):
            client = main.app.test_client()
            resp = client.get("/api/mal/callback?code=mal_auth_code_123&state=state_123")
            self.assertEqual(resp.status_code, 302)
            self.assertIn("mal_connected=1", resp.headers.get("Location", ""))

            auth = main.load_mal_auth()
            self.assertEqual(auth.get("access_token"), "new_access_token")
            self.assertEqual(auth.get("username"), "OtakuUser")
            mock_save_settings.assert_called_once()

    def test_mal_status_endpoint(self):
        main.save_mal_auth({"access_token": "tok_1", "username": "Otaku"})
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "load_settings", return_value={"mal_scrobble_enabled": True, "mal_client_id": "cid", "mal_scrobble_threshold": 85}):
            client = main.app.test_client()
            resp = client.get("/api/mal/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["connected"])
            self.assertEqual(data["username"], "Otaku")
            self.assertTrue(data["scrobble_enabled"])
            self.assertEqual(data["threshold"], 85)


if __name__ == "__main__":
    unittest.main()
