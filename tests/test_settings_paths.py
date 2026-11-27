import unittest
import tempfile
import os
import json
from unittest.mock import patch, MagicMock

from anibase.web import create_app
from anibase.web.routes_api import inspect_folder_path, analyze_path_hierarchy


class TestSettingsLibraryPaths(unittest.TestCase):
    def setUp(self):
        from anibase import constants as _constants
        from anibase.db import init_db
        from anibase.settings import get_default_settings, save_settings

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_settings_file = os.path.join(self.tmp_dir.name, "settings.json")
        self.test_db_path = os.path.join(self.tmp_dir.name, "test_anibase.db")
        self.test_history_file = os.path.join(self.tmp_dir.name, "watch_history.json")
        self.test_status_file = os.path.join(self.tmp_dir.name, "watch_status.json")
        init_db(self.test_db_path)

        self.setup_patchers = [
            patch.object(_constants, "SETTINGS_FILE", self.test_settings_file),
            patch.object(_constants, "DB_PATH", self.test_db_path),
            patch.object(_constants, "WATCH_HISTORY_FILE", self.test_history_file),
            patch.object(_constants, "WATCH_STATUS_FILE", self.test_status_file),
            patch("anibase.web.helpers.is_setup_complete", return_value=True),
        ]
        for p in self.setup_patchers:
            p.start()

        defaults = get_default_settings()
        defaults["setup_completed"] = True
        save_settings(defaults)

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for p in reversed(self.setup_patchers):
            p.stop()
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
        from anibase.settings import get_action_token
        token = get_action_token()

        # Missing token must be rejected
        res_unauth = self.client.post("/api/settings/test-discord", json={"client_id": "123456789012345678"})
        self.assertEqual(res_unauth.status_code, 403)

        with patch("anibase.web.routes_api.load_settings") as mock_load:
            mock_load.return_value = {"discord_client_id": ""}
            # Empty client ID
            res = self.client.post(
                "/api/settings/test-discord",
                headers={"X-Action-Token": token},
                json={"client_id": ""},
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertFalse(data["ok"])
            self.assertFalse(data["connected"])
            self.assertEqual(data["status"], "missing_id")

        # Invalid numeric client ID
        res = self.client.post(
            "/api/settings/test-discord",
            headers={"X-Action-Token": token},
            json={"client_id": "abc"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "invalid_id")

        # Mock successful connection
        with patch("anibase.constants.Presence") as mock_presence:
            mock_inst = MagicMock()
            mock_presence.return_value = mock_inst
            res_ok = self.client.post(
                "/api/settings/test-discord",
                headers={"X-Action-Token": token},
                json={"client_id": "123456789012345678"},
            )
            self.assertEqual(res_ok.status_code, 200)
            data_ok = res_ok.get_json()
            self.assertTrue(data_ok["ok"])
            self.assertTrue(data_ok["connected"])
            self.assertEqual(data_ok["status"], "connected")

    @patch("requests.get")
    def test_api_settings_test_lendrive_endpoint(self, mock_get):
        from anibase.settings import get_action_token
        token = get_action_token()

        # Missing token must be rejected
        res_unauth = self.client.post("/api/settings/test-lendrive", json={"base_url": "https://lendrive.web.id"})
        self.assertEqual(res_unauth.status_code, 403)

        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><h1>Lendrive Anime</h1></body></html>"

        res = self.client.post(
            "/api/settings/test-lendrive",
            headers={"X-Action-Token": token},
            json={
                "base_url": "https://lendrive.web.id",
                "cookie": "wssplashchk=valid_test_cookie"
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["valid"])
        self.assertFalse(data["challenge"])

        # Test challenge page detection
        mock_resp.status_code = 403
        mock_resp.text = "<html><title>Just a moment...</title><body>Checking your browser</body></html>"
        res2 = self.client.post(
            "/api/settings/test-lendrive",
            headers={"X-Action-Token": token},
            json={
                "base_url": "https://lendrive.web.id",
                "cookie": ""
            },
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2["ok"])
        self.assertFalse(data2["valid"])
        self.assertTrue(data2["challenge"])

        # Reject private/loopback SSRF URLs and never call requests.get
        mock_get.reset_mock()
        for bad_url in (
            "http://127.0.0.1:5000/internal",
            "http://localhost:8080/admin",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ):
            res_bad = self.client.post(
                "/api/settings/test-lendrive",
                headers={"X-Action-Token": token},
                json={"base_url": bad_url},
            )
            self.assertEqual(res_bad.status_code, 400)
            self.assertFalse(res_bad.get_json()["ok"])
        mock_get.assert_not_called()

        # Saved lendrive_cookie must not be leaked to a different untrusted host
        with patch("anibase.web.routes_api.load_settings", return_value={
            "lendrive_base_url": "https://lendrive.web.id",
            "lendrive_cookie": "secret_session_cookie",
        }):
            mock_resp.status_code = 200
            mock_resp.text = "<html>OK</html>"
            res_other = self.client.post(
                "/api/settings/test-lendrive",
                headers={"X-Action-Token": token},
                json={"base_url": "https://other.public.example"},
            )
            self.assertEqual(res_other.status_code, 200)
            sent_headers = mock_get.call_args.kwargs.get("headers", {})
            self.assertNotIn("Cookie", sent_headers)

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

    def test_auto_import_interval_and_stability_persistence(self):
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
                "auto_import_enabled": False,
                "auto_import_interval_seconds": 15,
                "auto_import_stable_seconds": 60,
            }

            res = self.client.post("/settings", data={
                "action_token": token,
                "auto_import_enabled": "on",
                "auto_import_interval_seconds": "45",
                "auto_import_stable_seconds": "90",
            }, headers={"X-Action-Token": token})

            self.assertEqual(res.status_code, 302)
            saved = mock_save.call_args[0][0]
            self.assertTrue(saved.get("auto_import_enabled"))
            self.assertEqual(saved.get("auto_import_interval_seconds"), 45)
            self.assertEqual(saved.get("auto_import_stable_seconds"), 90)

    def test_resolve_auto_import_ajax_json_response(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        dl_dir = os.path.join(self.tmp_dir.name, "Downloads")
        lib_dir = os.path.join(self.tmp_dir.name, "Library")
        target_dir = os.path.join(lib_dir, "Sousou no Frieren")
        os.makedirs(dl_dir, exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)

        source_file = os.path.join(dl_dir, "[SubsPlease] Sousou no Frieren - 01 (1080p).mkv")
        with open(source_file, "wb") as f:
            f.write(b"dummy video content")

        with patch("anibase.web.routes_api.load_settings") as mock_load, \
             patch("anibase.web.routes_api.save_settings"), \
             patch("anibase.web.routes_api.find_anime_path", return_value=target_dir), \
             patch("anibase.web.routes_api.is_valid_auto_import_resolve_source", return_value=True):

            mock_load.return_value = {
                "action_token": token,
                "auto_import_mappings": {},
            }

            # 1. AJAX JSON request
            res = self.client.post("/settings/auto-import/resolve", data={
                "action_token": token,
                "source_path": source_file,
                "target_anime": "Sousou no Frieren",
                "add_mapping": "1",
            }, headers={
                "Accept": "application/json",
                "X-Action-Token": token,
            })

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["target_anime"], "Sousou no Frieren")
            self.assertTrue(data["mapping_added"])
            self.assertEqual(data["filename"], "[SubsPlease] Sousou no Frieren - 01 (1080p).mkv")

            # 2. Invalid source reject JSON (status 422)
            with patch("anibase.web.routes_api.is_valid_auto_import_resolve_source", return_value=False):
                res_bad = self.client.post("/settings/auto-import/resolve", data={
                    "action_token": token,
                    "source_path": "/invalid/fake/path.mkv",
                    "target_anime": "Sousou no Frieren",
                }, headers={
                    "Accept": "application/json",
                    "X-Action-Token": token,
                })
                self.assertEqual(res_bad.status_code, 422)
                bad_data = res_bad.get_json()
                self.assertFalse(bad_data.get("ok", True))

    def test_cleanup_anime_cache_comprehensive_subtitles_and_media(self):
        from anibase import constants as _constants
        from anibase.scanner import cleanup_anime_cache

        anime_name = "Oshi no Ko"
        safe_name = "Oshi_no_Ko"

        # Mock cache directories inside temp dir
        test_cache = os.path.join(self.tmp_dir.name, "cache")
        test_subtitles = os.path.join(test_cache, "subtitles")
        test_posters = os.path.join(test_cache, "posters")
        test_banners = os.path.join(test_cache, "banners")
        test_metadata = os.path.join(test_cache, "metadata")
        test_characters = os.path.join(test_cache, "characters")
        test_episodes = os.path.join(test_cache, "episodes")
        test_movies = os.path.join(test_cache, "Movies")

        os.makedirs(os.path.join(test_subtitles, safe_name), exist_ok=True)
        os.makedirs(test_posters, exist_ok=True)
        os.makedirs(test_banners, exist_ok=True)
        os.makedirs(test_metadata, exist_ok=True)
        os.makedirs(os.path.join(test_characters, safe_name), exist_ok=True)
        os.makedirs(test_episodes, exist_ok=True)
        os.makedirs(test_movies, exist_ok=True)

        # Create files
        sub_file_in_dir = os.path.join(test_subtitles, safe_name, "Ep1.clean-v3.vtt")
        sub_file_root = os.path.join(test_subtitles, f"{safe_name}_Ep1.vtt")
        movie_sub_file = os.path.join(test_movies, f"{safe_name}.clean-v3.vtt")
        poster_file = os.path.join(test_posters, f"{safe_name}.webp")
        banner_file = os.path.join(test_banners, f"{safe_name}.png")
        meta_file = os.path.join(test_metadata, f"{safe_name}.json")
        ep_file = os.path.join(test_episodes, f"{safe_name}.json")

        for fpath in (sub_file_in_dir, sub_file_root, movie_sub_file, poster_file, banner_file, meta_file, ep_file):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("content")

        with patch.object(_constants, "CACHE_DIR", test_cache), \
             patch.object(_constants, "SUBTITLE_CACHE", test_subtitles), \
             patch.object(_constants, "POSTER_CACHE", test_posters), \
             patch.object(_constants, "BANNER_CACHE", test_banners), \
             patch.object(_constants, "METADATA_CACHE", test_metadata), \
             patch.object(_constants, "CHARACTER_CACHE", test_characters), \
             patch.object(_constants, "EPISODE_CACHE", test_episodes), \
             patch("anibase.scanner.get_active_cache_dir", return_value=os.path.abspath(test_cache)):

            summary = cleanup_anime_cache(anime_name)

            self.assertFalse(os.path.exists(sub_file_in_dir))
            self.assertFalse(os.path.exists(os.path.join(test_subtitles, safe_name)))
            self.assertFalse(os.path.exists(sub_file_root))
            self.assertFalse(os.path.exists(movie_sub_file))
            self.assertFalse(os.path.exists(poster_file))
            self.assertFalse(os.path.exists(banner_file))
            self.assertFalse(os.path.exists(meta_file))
            self.assertFalse(os.path.exists(ep_file))
            self.assertGreaterEqual(summary["removed_files"], 6)
            self.assertGreaterEqual(summary["removed_dirs"], 1)

    def test_cleanup_all_subtitles_and_thumbnails_cache(self):
        from anibase import constants as _constants
        from anibase.scanner import cleanup_all_subtitles_cache, cleanup_all_thumbnails_cache

        test_cache = os.path.join(self.tmp_dir.name, "cache2")
        test_sub = os.path.join(test_cache, "all_subs")
        test_thumb = os.path.join(test_cache, "all_thumbs")
        os.makedirs(os.path.join(test_sub, "Anime1"), exist_ok=True)
        os.makedirs(os.path.join(test_thumb, "seek", "12345"), exist_ok=True)

        with open(os.path.join(test_sub, "Anime1", "sub.vtt"), "w") as f:
            f.write("test")
        with open(os.path.join(test_thumb, "thumb1.jpg"), "w") as f:
            f.write("test")

        with patch.object(_constants, "CACHE_DIR", test_cache), \
             patch.object(_constants, "SUBTITLE_CACHE", test_sub), \
             patch.object(_constants, "THUMBNAIL_CACHE", test_thumb), \
             patch("anibase.scanner.get_active_cache_dir", return_value=os.path.abspath(test_cache)):

            summary_sub = cleanup_all_subtitles_cache()
            self.assertEqual(len(os.listdir(test_sub)), 0)
            self.assertGreater(summary_sub["removed_dirs"], 0)

            summary_thumb = cleanup_all_thumbnails_cache()
            self.assertEqual(len(os.listdir(test_thumb)), 0)
            self.assertGreater(summary_thumb["removed_files"] + summary_thumb["removed_dirs"], 0)

    def test_optimize_database(self):
        from anibase.scanner import optimize_database
        res = optimize_database()
        self.assertTrue(res.get("ok"))
        self.assertIn("berhasil", res.get("message", "").lower())

    def test_cache_size_breakdown(self):
        from anibase.scanner import get_cache_size_breakdown, format_size_bytes
        self.assertEqual(format_size_bytes(0), "0 B")
        self.assertEqual(format_size_bytes(1024), "1.0 KB")
        self.assertEqual(format_size_bytes(1024 * 1024 * 5), "5.0 MB")

        sizes = get_cache_size_breakdown()
        for key in ("posters", "thumbnails", "subtitles", "metadata", "database", "total"):
            self.assertIn(key, sizes)
            self.assertIn("bytes", sizes[key])
            self.assertIn("formatted", sizes[key])
            self.assertIsInstance(sizes[key]["bytes"], (int, float))

    def test_maintenance_routes(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        # 1. GET /api/settings/cache-size
        res_size = self.client.get("/api/settings/cache-size")
        self.assertEqual(res_size.status_code, 200)
        data_size = res_size.get_json()
        self.assertTrue(data_size["ok"])
        self.assertIn("sizes", data_size)

        # 2. POST /settings/cleanup-cache (AJAX json orphan)
        res_orphan = self.client.post("/settings/cleanup-cache", json={
            "action_token": token,
            "target": "orphan"
        }, headers={"X-Action-Token": token})
        self.assertEqual(res_orphan.status_code, 200)
        data_orphan = res_orphan.get_json()
        self.assertTrue(data_orphan["ok"])
        self.assertEqual(data_orphan["target"], "orphan")
        self.assertIn("sizes", data_orphan)

        # 3. POST /settings/cleanup-cache (AJAX json subtitles)
        res_sub = self.client.post("/settings/cleanup-cache", json={
            "action_token": token,
            "target": "subtitles"
        }, headers={"X-Action-Token": token})
        self.assertEqual(res_sub.status_code, 200)
        data_sub = res_sub.get_json()
        self.assertTrue(data_sub["ok"])
        self.assertEqual(data_sub["target"], "subtitles")

        # 4. POST /settings/cleanup-cache (AJAX json vacuum)
        res_vac = self.client.post("/settings/cleanup-cache", json={
            "action_token": token,
            "target": "vacuum"
        }, headers={"X-Action-Token": token})
        self.assertEqual(res_vac.status_code, 200)
        data_vac = res_vac.get_json()
        self.assertTrue(data_vac["ok"])
        self.assertEqual(data_vac["target"], "vacuum")

        # 5. POST /settings/cleanup-cache (Standard Form Submit)
        res_form = self.client.post("/settings/cleanup-cache", data={
            "action_token": token,
            "target": "orphan"
        }, headers={"X-Action-Token": token})
        self.assertEqual(res_form.status_code, 302)
        self.assertIn("cache_cleaned=1", res_form.headers["Location"])

    def test_export_settings_backup_full_and_selective(self):
        # 1. Full export (all sections included)
        res_full = self.client.get("/settings/backup/export")
        self.assertEqual(res_full.status_code, 200)
        self.assertIn("attachment", res_full.headers.get("Content-Disposition", ""))
        self.assertIn("anibase-backup-", res_full.headers.get("Content-Disposition", ""))

        payload_full = json.loads(res_full.data.decode("utf-8"))
        self.assertEqual(payload_full["format"], "anibase-settings-backup")
        self.assertEqual(payload_full["version"], 2)
        self.assertIn("settings", payload_full)
        self.assertNotIn("action_token", payload_full["settings"])
        self.assertIn("mappings", payload_full)
        self.assertIn("watch_history", payload_full)
        self.assertIn("watch_status", payload_full)
        self.assertIn("user_reviews", payload_full)
        self.assertIn("settings", payload_full["included_sections"])
        self.assertIn("mappings", payload_full["included_sections"])
        self.assertIn("watch_history", payload_full["included_sections"])

        # 2. Selective export (only settings and mappings)
        res_sel = self.client.get("/settings/backup/export?include_settings=1&include_mappings=1&include_watch_history=0&include_reviews=0")
        self.assertEqual(res_sel.status_code, 200)
        payload_sel = json.loads(res_sel.data.decode("utf-8"))
        self.assertIn("settings", payload_sel)
        self.assertIn("mappings", payload_sel)
        self.assertNotIn("watch_history", payload_sel)
        self.assertNotIn("user_reviews", payload_sel)
        self.assertEqual(payload_sel["included_sections"], ["settings", "mappings"])

    def test_inspect_settings_backup(self):
        import io
        from anibase.settings import get_action_token
        token = get_action_token()

        valid_backup = {
            "format": "anibase-settings-backup",
            "version": 2,
            "created_at": "2026-11-19T10:00:00Z",
            "app_version": "1.5.3",
            "settings": {"theme_preset": "dark-violet", "player_auto_next": True},
            "mappings": {
                "anilist_mappings": {"Sousou no Frieren": {"anilist_id": 154587}},
                "auto_import_mappings": {"/Downloads": "/Anime"},
                "episode_display_names": {"Sousou no Frieren": {"ep1.mkv": "Episode 1"}},
            },
            "watch_history": {"Sousou no Frieren": {"progress": 100}},
            "watch_status": {"Sousou no Frieren": {"ep1.mkv": {"watched": True}}},
            "user_reviews": [{"anime_name": "Sousou no Frieren", "score": 10, "comments": "Masterpiece"}],
        }

        # 1. Valid backup inspection
        data = io.BytesIO(json.dumps(valid_backup).encode("utf-8"))
        res = self.client.post(
            "/api/settings/backup/inspect",
            data={"backup_file": (data, "backup.json"), "action_token": token},
            content_type="multipart/form-data",
            headers={"X-Action-Token": token}
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], 2)
        sum_data = body["summary"]
        self.assertTrue(sum_data["has_settings"])
        self.assertEqual(sum_data["settings_count"], 2)
        self.assertTrue(sum_data["has_mappings"])
        self.assertEqual(sum_data["anilist_mappings_count"], 1)
        self.assertEqual(sum_data["auto_import_mappings_count"], 1)
        self.assertEqual(sum_data["episode_display_names_count"], 1)
        self.assertTrue(sum_data["has_watch_history"])
        self.assertEqual(sum_data["watch_history_count"], 1)
        self.assertEqual(sum_data["watch_status_count"], 1)
        self.assertTrue(sum_data["has_reviews"])
        self.assertEqual(sum_data["review_count"], 1)

        # 2. Invalid JSON format rejection
        bad_json = io.BytesIO(b"not a valid json string")
        res_bad = self.client.post(
            "/api/settings/backup/inspect",
            data={"backup_file": (bad_json, "bad.json"), "action_token": token},
            content_type="multipart/form-data",
            headers={"X-Action-Token": token}
        )
        self.assertEqual(res_bad.status_code, 400)
        self.assertFalse(res_bad.get_json()["ok"])

        # 3. Invalid format marker rejection
        wrong_fmt = io.BytesIO(json.dumps({"format": "other-app", "version": 1}).encode("utf-8"))
        res_fmt = self.client.post(
            "/api/settings/backup/inspect",
            data={"backup_file": (wrong_fmt, "wrong.json"), "action_token": token},
            content_type="multipart/form-data",
            headers={"X-Action-Token": token}
        )
        self.assertEqual(res_fmt.status_code, 400)

    def test_import_settings_backup_ajax_and_form(self):
        import io
        from anibase.settings import get_action_token
        token = get_action_token()

        backup_payload = {
            "format": "anibase-settings-backup",
            "version": 2,
            "created_at": "2026-11-19T10:00:00Z",
            "settings": {"theme_preset": "oled-black", "screenshot_filename_prefix": "anibase_shot"},
            "mappings": {
                "anilist_mappings": {"Frieren": {"anilist_id": 154587}},
                "auto_import_mappings": {},
                "episode_display_names": {},
            },
            "watch_history": {"Frieren": {"media_name": "Frieren", "time": 120, "duration": 1400}},
            "watch_status": {"Frieren": {"ep1.mkv": {"watched": True, "progress": 100}}},
            "user_reviews": [{"anime_name": "Frieren", "score": 9, "comments": "Great show"}],
        }

        # 1. AJAX JSON restore
        data_io = io.BytesIO(json.dumps(backup_payload).encode("utf-8"))
        res_ajax = self.client.post(
            "/settings/backup/import",
            data={
                "backup_file": (data_io, "backup.json"),
                "action_token": token,
                "restore_settings": "1",
                "restore_mappings": "1",
                "restore_watch_history": "1",
                "restore_reviews": "1",
            },
            content_type="multipart/form-data",
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-Action-Token": token
            }
        )
        self.assertEqual(res_ajax.status_code, 200)
        ajax_body = res_ajax.get_json()
        self.assertTrue(ajax_body["ok"])
        self.assertIn("Preferences", ajax_body["sections_restored"])
        self.assertIn("Mappings", ajax_body["sections_restored"])
        self.assertIn("Watch History & Progress", ajax_body["sections_restored"])
        self.assertIn("1 Reviews", ajax_body["sections_restored"])

        # 2. Standard Form submit restore (redirect)
        data_io2 = io.BytesIO(json.dumps(backup_payload).encode("utf-8"))
        res_form = self.client.post(
            "/settings/backup/import",
            data={
                "backup_file": (data_io2, "backup.json"),
                "action_token": token,
            },
            content_type="multipart/form-data",
            headers={"X-Action-Token": token}
        )
        self.assertEqual(res_form.status_code, 302)
        self.assertIn("backup_imported=1", res_form.headers["Location"])

    def test_export_settings_backup_post_requires_action_token_and_security_headers(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        res_missing = self.client.post("/settings/backup/export", data={"include_settings": "1"})
        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_missing.get_json()["error"], "invalid_action_token")
        self.assertEqual(res_missing.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res_missing.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(res_missing.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

        res_valid = self.client.post(
            "/settings/backup/export",
            data={"include_settings": "1", "action_token": token},
        )
        self.assertEqual(res_valid.status_code, 200)
        payload = json.loads(res_valid.data.decode("utf-8"))
        self.assertEqual(payload["format"], "anibase-settings-backup")
        self.assertEqual(res_valid.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res_valid.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(res_valid.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")


if __name__ == "__main__":
    unittest.main()
