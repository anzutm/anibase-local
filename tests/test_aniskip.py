"""Tests for AniSkip opening/ending skip integration and caching."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests

import main


class AniSkipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache_dir = self.root / "cache" / "aniskip"
        self.original_cache = main.ANISKIP_CACHE
        main.ANISKIP_CACHE = str(self.cache_dir)
        self.addCleanup(self.restore_cache)

        self.video = self.root / "Episode 01.mkv"
        self.video.write_bytes(b"video")

    def restore_cache(self):
        main.ANISKIP_CACHE = self.original_cache

    def test_get_aniskip_times_success_and_caching(self):
        sample_api_response = {
            "found": True,
            "results": [
                {
                    "interval": {"startTime": 85.5, "endTime": 175.5},
                    "skipType": "op",
                    "skipId": "op-1",
                    "episodeLength": 1420
                },
                {
                    "interval": {"startTime": 1320.0, "endTime": 1410.0},
                    "skipType": "ed",
                    "skipId": "ed-1",
                    "episodeLength": 1420
                }
            ],
            "statusCode": 200
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_api_response

        with patch.object(main.requests, "get", return_value=mock_resp) as mock_get:
            result = main.get_aniskip_times(mal_id=1234, episode_num=1, episode_length=1420)
            self.assertTrue(result["found"])
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(result["results"][0]["type"], "op")
            self.assertEqual(result["results"][0]["name"], "Opening")
            self.assertEqual(result["results"][0]["start"], 85.5)
            self.assertEqual(result["results"][0]["end"], 175.5)
            self.assertEqual(result["results"][1]["type"], "ed")
            self.assertEqual(result["results"][1]["name"], "Ending")
            mock_get.assert_called_once()

        # Cache file must exist
        cache_file = self.cache_dir / "1234_1.json"
        self.assertTrue(cache_file.is_file())

        # Second call must hit cache without network requests
        with patch.object(main.requests, "get") as mock_get_second:
            cached_result = main.get_aniskip_times(mal_id=1234, episode_num=1)
            self.assertTrue(cached_result["found"])
            self.assertEqual(len(cached_result["results"]), 2)
            mock_get_second.assert_not_called()

    def test_get_aniskip_times_404_caching(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch.object(main.requests, "get", return_value=mock_resp):
            result = main.get_aniskip_times(mal_id=9999, episode_num=5)
            self.assertFalse(result["found"])
            self.assertEqual(result["results"], [])

        # Negative result is cached
        cache_file = self.cache_dir / "9999_5.json"
        self.assertTrue(cache_file.is_file())

    def test_get_aniskip_times_network_failure_handled(self):
        with patch.object(main.requests, "get", side_effect=requests.RequestException("connection timed out")):
            result = main.get_aniskip_times(mal_id=5555, episode_num=2)
            self.assertFalse(result["found"])
            self.assertEqual(result["reason"], "network_unavailable")

    def test_get_aniskip_times_invalid_params(self):
        self.assertEqual(main.get_aniskip_times(None, 1)["reason"], "invalid_parameters")
        self.assertEqual(main.get_aniskip_times(1234, 0)["reason"], "invalid_parameters")
        self.assertEqual(main.get_aniskip_times(1234, -1)["reason"], "invalid_parameters")

    def test_api_skip_times_endpoint_success(self):
        mock_skip_data = {
            "found": True,
            "results": [
                {"type": "op", "name": "Opening", "start": 90.0, "end": 180.0}
            ]
        }
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "find_media_path", return_value=str(self.root)), \
             patch.object(main, "get_cached_anilist_info", return_value={"mal_id": 4321}), \
             patch.object(main, "get_aniskip_times", return_value=mock_skip_data):
            client = main.app.test_client()
            resp = client.get("/api/media/Example/Episode 01.mkv/skip-times")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "success")
            self.assertTrue(data["found"])
            self.assertEqual(len(data["results"]), 1)
            self.assertEqual(data["results"][0]["name"], "Opening")

    def test_api_skip_times_endpoint_no_mal_id(self):
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "find_media_path", return_value=str(self.root)), \
             patch.object(main, "get_cached_anilist_info", return_value={}), \
             patch.object(main, "get_metadata_mapping", return_value=None):
            client = main.app.test_client()
            resp = client.get("/api/media/Example/Episode 01.mkv/skip-times")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "success")
            self.assertFalse(data["found"])
            self.assertEqual(data["reason"], "no_mal_id")

    def test_api_skip_times_anime_not_found(self):
        with patch.object(main, "is_setup_complete", return_value=True), \
             patch.object(main, "find_media_path", return_value=None):
            client = main.app.test_client()
            resp = client.get("/api/media/NonExistent/Episode 01.mkv/skip-times")
            self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
