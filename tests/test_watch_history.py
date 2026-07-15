import json
import html
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main
from werkzeug.exceptions import NotFound


class HiddenSubprocessTests(unittest.TestCase):
    def setUp(self):
        self.original_hidden_kwargs = main.hidden_subprocess_kwargs
        self.original_run = main.subprocess.run
        self.original_popen = main.subprocess.Popen

    def tearDown(self):
        main.hidden_subprocess_kwargs = self.original_hidden_kwargs
        main.subprocess.run = self.original_run
        main.subprocess.Popen = self.original_popen

    def test_run_hidden_subprocess_merges_hide_console_options(self):
        calls = {}

        def fake_hidden_kwargs():
            return {
                "creationflags": 0x08000000,
                "startupinfo": "hidden-startupinfo",
            }

        def fake_run(args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return main.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        main.hidden_subprocess_kwargs = fake_hidden_kwargs
        main.subprocess.run = fake_run

        result = main.run_hidden_subprocess(["ffprobe", "-version"], creationflags=2, timeout=1)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls["args"], ["ffprobe", "-version"])
        self.assertEqual(calls["kwargs"]["creationflags"], 0x08000000 | 2)
        self.assertEqual(calls["kwargs"]["startupinfo"], "hidden-startupinfo")
        self.assertNotIn("shell", calls["kwargs"])

    def test_popen_hidden_subprocess_applies_hide_console_options(self):
        calls = {}

        def fake_hidden_kwargs():
            return {
                "creationflags": 0x08000000,
                "startupinfo": "hidden-startupinfo",
            }

        def fake_popen(args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return object()

        main.hidden_subprocess_kwargs = fake_hidden_kwargs
        main.subprocess.Popen = fake_popen

        process = main.popen_hidden_subprocess(["vlc.exe", "episode.mkv"])

        self.assertIsNotNone(process)
        self.assertEqual(calls["args"], ["vlc.exe", "episode.mkv"])
        self.assertEqual(calls["kwargs"]["creationflags"], 0x08000000)
        self.assertEqual(calls["kwargs"]["startupinfo"], "hidden-startupinfo")
        self.assertNotIn("shell", calls["kwargs"])


class DiscordRpcLifecycleTests(unittest.TestCase):
    class FakePresence:
        def __init__(self, client_id):
            self.client_id = client_id
            self.connected = False
            self.closed = False
            self.clear_count = 0
            self.updates = []

        def connect(self):
            self.connected = True

        def update(self, **payload):
            self.updates.append(payload)

        def clear(self):
            self.clear_count += 1

        def close(self):
            self.closed = True

    def setUp(self):
        self.original_presence = main.Presence
        self.original_enabled = main.DISCORD_RPC_ENABLED
        self.original_client_id = main.DISCORD_CLIENT_ID
        main.Presence = self.FakePresence
        main.DISCORD_RPC_ENABLED = True
        main.DISCORD_CLIENT_ID = "test-client"
        main.reset_discord_rpc()

    def tearDown(self):
        main.reset_discord_rpc()
        main.Presence = self.original_presence
        main.DISCORD_RPC_ENABLED = self.original_enabled
        main.DISCORD_CLIENT_ID = self.original_client_id

    def test_update_sets_elapsed_timer_and_local_button(self):
        main.update_discord_rpc("Demo", 2, "01:00 / 24:00", owner_id="tab-a")

        payload = main.rpc.updates[-1]
        self.assertIsInstance(payload["start"], int)
        self.assertEqual(payload["buttons"][0]["url"], "http://127.0.0.1:5000")
        self.assertEqual(main.CURRENT_RPC_OWNER, "tab-a")

    def test_stale_tab_cannot_clear_current_owner(self):
        main.update_discord_rpc("Demo", 1, owner_id="tab-a")
        main.update_discord_rpc("Other", 2, owner_id="tab-b")

        self.assertFalse(main.clear_discord_rpc("tab-a"))
        self.assertEqual(main.CURRENT_RPC_OWNER, "tab-b")
        self.assertEqual(main.rpc.clear_count, 0)

        self.assertTrue(main.clear_discord_rpc("tab-b"))
        self.assertIsNone(main.CURRENT_RPC_OWNER)
        self.assertEqual(main.rpc.clear_count, 1)

    def test_process_monitor_only_clears_its_own_presence(self):
        class FinishedProcess:
            def wait(self):
                return 0

        main.update_discord_rpc("Browser", 1, owner_id="tab-a")
        main.clear_discord_rpc_when_process_exits(FinishedProcess(), "old-vlc")

        self.assertEqual(main.CURRENT_RPC_OWNER, "tab-a")
        self.assertEqual(main.rpc.clear_count, 0)


class WatchHistoryRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.original_watch_history_file = main.WATCH_HISTORY_FILE
        main.WATCH_HISTORY_FILE = os.path.join(self.temp_dir.name, "watch_history.json")

        with open(main.WATCH_HISTORY_FILE, "w", encoding="utf-8") as handle:
            json.dump({"demo-anime": {"episode": "1"}}, handle)

    def tearDown(self):
        main.WATCH_HISTORY_FILE = self.original_watch_history_file

    def test_remove_watch_history_entry(self):
        removed = main.remove_watch_history_entry("demo-anime")

        self.assertTrue(removed)

        with open(main.WATCH_HISTORY_FILE, "r", encoding="utf-8") as handle:
            saved_history = json.load(handle)

        self.assertNotIn("demo-anime", saved_history)

        self.assertFalse(main.remove_watch_history_entry("missing-entry"))


class LoggingSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_dir = os.path.join(self.temp_dir.name, "logs")
        self.original_handlers = list(main.LOGGER.handlers)
        self.original_configured = main.LOGGING_CONFIGURED
        self.original_config = main.LOGGING_CONFIG
        self.original_startup_logged = main.STARTUP_SUMMARY_LOGGED
        self.original_get_media_dependency_diagnostics = main.get_media_dependency_diagnostics
        self.original_settings_file = main.SETTINGS_FILE
        self.original_auto_import_thread = main.AUTO_IMPORT_THREAD
        self.original_thread_class = main.threading.Thread
        self.original_sync_lock_locked = main.LIBRARY_SYNC_LOCK.locked()

        if self.original_sync_lock_locked:
            self.fail("Library sync lock unexpectedly locked before logging test")

        main.SETTINGS_FILE = os.path.join(self.temp_dir.name, "settings.json")
        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.temp_dir.name],
            "action_token": "test-token",
        })
        main.save_settings(settings)

        for handler in list(main.LOGGER.handlers):
            if getattr(handler, "_anibase_managed", False):
                main.LOGGER.removeHandler(handler)
                handler.close()
        main.LOGGING_CONFIGURED = False
        main.LOGGING_CONFIG = None
        main.STARTUP_SUMMARY_LOGGED = False

    def tearDown(self):
        if main.LIBRARY_SYNC_LOCK.locked():
            main.LIBRARY_SYNC_LOCK.release()
        for handler in list(main.LOGGER.handlers):
            if getattr(handler, "_anibase_managed", False):
                main.LOGGER.removeHandler(handler)
                handler.close()
        for handler in self.original_handlers:
            if handler not in main.LOGGER.handlers:
                main.LOGGER.addHandler(handler)
        main.LOGGING_CONFIGURED = self.original_configured
        main.LOGGING_CONFIG = self.original_config
        main.STARTUP_SUMMARY_LOGGED = self.original_startup_logged
        main.get_media_dependency_diagnostics = self.original_get_media_dependency_diagnostics
        main.SETTINGS_FILE = self.original_settings_file
        main.AUTO_IMPORT_THREAD = self.original_auto_import_thread
        main.threading.Thread = self.original_thread_class

    def read_log(self):
        for handler in main.LOGGER.handlers:
            if hasattr(handler, "flush"):
                handler.flush()
        log_path = os.path.join(self.log_dir, "anibase.log")
        if not os.path.exists(log_path):
            return ""
        with open(log_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_logging_setup_is_idempotent(self):
        main.configure_logging(log_dir=self.log_dir)
        first_handlers = list(main.LOGGER.handlers)
        main.configure_logging(log_dir=self.log_dir)

        self.assertEqual(first_handlers, list(main.LOGGER.handlers))
        self.assertEqual(len(first_handlers), 2)

    def test_rotating_file_handler_uses_utf8_and_writes(self):
        main.configure_logging(log_dir=self.log_dir, max_bytes=1024, backup_count=1)
        file_handlers = [
            handler for handler in main.LOGGER.handlers
            if isinstance(handler, main.RotatingFileHandler)
        ]

        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(file_handlers[0].encoding.lower().replace("-", ""), "utf8")

        main.app_log("Unicode log test 試験", "INFO")

        self.assertIn("Unicode log test 試験", self.read_log())

    def test_log_directory_created_and_rotation_occurs(self):
        main.configure_logging(log_dir=self.log_dir, max_bytes=120, backup_count=1)
        self.assertTrue(os.path.isdir(self.log_dir))

        for index in range(20):
            main.app_log(f"rotation line {index} {'x' * 40}", "INFO")

        self.assertTrue(os.path.exists(os.path.join(self.log_dir, "anibase.log")))
        self.assertTrue(os.path.exists(os.path.join(self.log_dir, "anibase.log.1")))

    def test_action_token_and_invalid_body_are_not_logged(self):
        main.configure_logging(log_dir=self.log_dir)
        client = main.app.test_client()
        secret = "super-secret-token-value"

        response = client.post(
            "/update_progress",
            data=f'{{"action_token":"{secret}",',
            content_type="application/json",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(secret, self.read_log())

    def test_internal_exception_logged_but_response_is_safe(self):
        main.configure_logging(log_dir=self.log_dir)
        client = main.app.test_client()
        original_sync_all_library = main.sync_all_library

        def failing_sync(_label):
            raise RuntimeError("very secret internal path C:/Private")

        main.sync_all_library = failing_sync
        try:
            response = client.post(
                "/setup/sync",
                headers={"X-AniBase-Action-Token": "test-token"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        finally:
            main.sync_all_library = original_sync_all_library

        body = response.get_data(as_text=True)
        log_text = self.read_log()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Setup sync failed", log_text)
        self.assertNotIn("C:/Private", body)

    def test_startup_summary_logs_once(self):
        main.configure_logging(log_dir=self.log_dir)
        main.get_media_dependency_diagnostics = lambda _settings: {
            "ffmpeg": {"available": True, "status": "available"},
            "ffprobe": {"available": False, "status": "not_found"},
            "vlc": {"available": False, "status": "not_configured"},
        }

        main.log_startup_summary("test", "127.0.0.1", 5000, True, True, False)
        main.log_startup_summary("test", "127.0.0.1", 5000, True, True, False)

        self.assertEqual(self.read_log().count("Startup summary:"), 1)

    def test_cache_hit_does_not_log_info(self):
        main.configure_logging(log_dir=self.log_dir)
        video_path = os.path.join(self.temp_dir.name, "Episode 01.mkv")
        with open(video_path, "wb") as handle:
            handle.write(b"video")
        thumbnail_path = main.get_thumbnail_cache_path(video_path)
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        with open(thumbnail_path, "wb") as handle:
            handle.write(b"thumb")

        result = main.get_thumbnail_result(video_path)

        self.assertTrue(result["ok"])
        self.assertNotIn("Thumbnail cache found", self.read_log())

    def test_sync_skip_logs_warning(self):
        main.configure_logging(log_dir=self.log_dir)
        self.assertTrue(main.LIBRARY_SYNC_LOCK.acquire(blocking=False))
        try:
            result = main.sync_all_library("Logging test sync")
        finally:
            if main.LIBRARY_SYNC_LOCK.locked():
                main.LIBRARY_SYNC_LOCK.release()

        self.assertEqual(result["reason"], "sync_in_progress")
        self.assertIn("WARNING", self.read_log())
        self.assertIn("Logging test sync skipped", self.read_log())

    def test_auto_import_disabled_does_not_log_polling(self):
        main.configure_logging(log_dir=self.log_dir)

        for _ in range(3):
            main.auto_import_scan_once(force_enabled=False)

        self.assertNotIn("Auto import inactive", self.read_log())

    def test_auto_import_thread_has_clear_name(self):
        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **_kwargs):
                self.target = target
                self.name = name
                self.daemon = daemon
                self.started = False

            def is_alive(self):
                return self.started

            def start(self):
                self.started = True

        main.threading.Thread = FakeThread
        main.AUTO_IMPORT_THREAD = None
        main.start_auto_import_worker()

        self.assertIsNotNone(main.AUTO_IMPORT_THREAD)
        self.assertEqual(main.AUTO_IMPORT_THREAD.name, "auto-import")


class MovieWatchSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.movie_file = "Demo Movie.mkv"
        open(os.path.join(self.temp_dir.name, self.movie_file), "w", encoding="utf-8").close()

        self.original_movie_path = main.MOVIE_PATH
        self.original_get_cached_anilist_info = main.get_cached_anilist_info
        self.original_get_episode_watch_status = main.get_episode_watch_status

        main.MOVIE_PATH = self.temp_dir.name
        main.get_cached_anilist_info = lambda _title: {}

    def tearDown(self):
        main.MOVIE_PATH = self.original_movie_path
        main.get_cached_anilist_info = self.original_get_cached_anilist_info
        main.get_episode_watch_status = self.original_get_episode_watch_status

    def test_movie_without_progress_is_not_started(self):
        movie = self._get_movie_with_status({})

        self.assertEqual(movie["watch_progress_label"], "0/1")
        self.assertEqual(movie["watch_status_label"], "NOT STARTED")
        self.assertEqual(movie["watch_status_kind"], "not_started")

    def test_movie_with_partial_progress_is_watching(self):
        movie = self._get_movie_with_status({"progress": 42, "watched": False})

        self.assertEqual(movie["watch_progress_label"], "0/1")
        self.assertEqual(movie["watch_status_label"], "WATCHING")
        self.assertEqual(movie["watch_status_kind"], "watching")

    def test_movie_with_finished_progress_is_completed(self):
        movie = self._get_movie_with_status({"progress": 93, "watched": False})

        self.assertEqual(movie["watch_progress_label"], "1/1")
        self.assertEqual(movie["watch_status_label"], "COMPLETED")
        self.assertEqual(movie["watch_status_kind"], "completed")

    def _get_movie_with_status(self, episode_status):
        main.get_episode_watch_status = lambda _anime_name, _episode: episode_status
        movies = main.get_movies()
        self.assertEqual(len(movies), 1)
        return movies[0]


class SettingsLibraryPathTests(unittest.TestCase):
    def test_legacy_watchlist_and_ongoing_paths_become_library_paths(self):
        settings = {
            "watchlist_path": "D:/Anime/Watchlist",
            "ongoing_path": "D:/Anime/Ongoing",
        }

        self.assertEqual(
            main.get_settings_library_paths(settings),
            [
                os.path.abspath("D:/Anime/Watchlist"),
                os.path.abspath("D:/Anime/Ongoing"),
            ],
        )

    def test_library_paths_are_deduplicated_and_ignore_legacy_paths(self):
        settings = {
            "library_paths": ["D:/Anime/Main", "D:/Anime/Main", ""],
            "watchlist_path": "D:/Anime/Legacy",
        }

        self.assertEqual(
            main.get_settings_library_paths(settings),
            [os.path.abspath("D:/Anime/Main")],
        )


class AutoImportMappingTests(unittest.TestCase):
    def test_build_auto_import_mappings_from_input_pairs(self):
        mappings = main.build_auto_import_mappings_from_pairs(
            [" Hokori ", "", "Wrong"],
            [" Heroine Seijo Iie, All Works Maid desu (Ko)! ", "Ignored", ""],
        )

        self.assertEqual(
            mappings,
            {
                "Hokori": "Heroine Seijo Iie, All Works Maid desu (Ko)!",
            },
        )

    def test_auto_import_target_root_uses_selected_library_path(self):
        with tempfile.TemporaryDirectory() as first_path, tempfile.TemporaryDirectory() as second_path:
            settings = {
                "library_paths": [first_path, second_path],
                "auto_import_destination_root": second_path,
            }

            self.assertEqual(
                main.get_auto_import_target_root(settings),
                os.path.abspath(second_path),
            )

    def test_auto_import_target_root_stays_empty_without_selection(self):
        with tempfile.TemporaryDirectory() as first_path:
            settings = {
                "library_paths": [first_path],
                "auto_import_destination_root": "",
            }

            self.assertEqual(main.get_auto_import_target_root(settings), "")

    def test_auto_import_target_matching_stays_inside_selected_destination(self):
        original_get_anime_folder_index = main.get_anime_folder_index
        with tempfile.TemporaryDirectory() as first_path, tempfile.TemporaryDirectory() as second_path:
            main.get_anime_folder_index = lambda: [
                {
                    "name": "Hokori Wrong Library",
                    "path": os.path.join(first_path, "Hokori Wrong Library"),
                    "base_path": first_path,
                },
                {
                    "name": "Hokori Correct Library",
                    "path": os.path.join(second_path, "Hokori Correct Library"),
                    "base_path": second_path,
                },
            ]
            settings = {
                "library_paths": [first_path, second_path],
                "auto_import_destination_root": second_path,
                "auto_import_mappings": {},
            }

            try:
                target, _, _ = main.resolve_auto_import_target("Hokori", settings)
            finally:
                main.get_anime_folder_index = original_get_anime_folder_index

            self.assertEqual(target["base_path"], os.path.abspath(second_path))


class NetworkAccessTests(unittest.TestCase):
    def test_local_client_addresses_are_allowed_as_local(self):
        self.assertTrue(main.is_local_client_address("127.0.0.1"))
        self.assertTrue(main.is_local_client_address("127.0.0.2"))
        self.assertTrue(main.is_local_client_address("::1"))

    def test_lan_client_address_is_not_local(self):
        self.assertFalse(main.is_local_client_address("192.168.1.20"))

    def test_private_addresses_are_lan_addresses(self):
        self.assertTrue(main.is_lan_client_address("192.168.1.20"))
        self.assertTrue(main.is_lan_client_address("10.0.0.5"))
        self.assertTrue(main.is_lan_client_address("172.16.0.5"))

    def test_public_address_is_not_lan_address(self):
        self.assertFalse(main.is_lan_client_address("8.8.8.8"))


class CharacterImageRouteSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_character_cache = main.CHARACTER_CACHE
        main.CHARACTER_CACHE = os.path.join(self.temp_dir.name, "characters")
        self.anime_name = "Demo Anime"
        self.safe_anime = main.safe_cache_name(self.anime_name)
        self.anime_dir = os.path.join(main.CHARACTER_CACHE, self.safe_anime)
        os.makedirs(self.anime_dir, exist_ok=True)

    def tearDown(self):
        main.CHARACTER_CACHE = self.original_character_cache

    def write_character_file(self, filename, content=b"image"):
        path = os.path.join(self.anime_dir, filename)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def call_character_img(self, filename):
        with main.app.test_request_context(f"/character_img/{self.anime_name}/{filename}"):
            return main.character_img(self.anime_name, filename)

    def assert_rejected(self, filename):
        with self.assertRaises(NotFound) as context:
            self.call_character_img(filename)

        self.assertNotIn(self.temp_dir.name, str(context.exception))

    def test_valid_character_image_is_served(self):
        self.write_character_file("Alice_char.jpg", b"valid")

        response = self.call_character_img("Alice_char.jpg")

        self.assertEqual(response.status_code, 200)
        response.close()

    def test_missing_character_image_returns_404(self):
        self.assert_rejected("missing_char.jpg")

    def test_dotdot_traversal_is_rejected(self):
        self.assert_rejected("../secret.txt")

    def test_encoded_traversal_is_rejected(self):
        self.assert_rejected("%2e%2e%2fsecret.txt")

    def test_backslash_traversal_is_rejected(self):
        self.assert_rejected(r"..\secret.txt")

    def test_absolute_path_is_rejected(self):
        outside = os.path.join(self.temp_dir.name, "outside.txt")
        with open(outside, "wb") as handle:
            handle.write(b"secret")

        self.assert_rejected(os.path.abspath(outside))

    def test_drive_letter_path_is_rejected(self):
        self.assert_rejected("C:secret.txt")

    def test_nested_path_is_rejected(self):
        self.assert_rejected("nested/file.jpg")

    def test_unc_path_is_rejected(self):
        self.assert_rejected(r"\\server\share\secret.jpg")

    def test_prefix_collision_path_is_rejected(self):
        backup_dir = os.path.join(self.temp_dir.name, "characters_backup")
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "secret.jpg"), "wb") as handle:
            handle.write(b"secret")

        self.assert_rejected(r"..\characters_backup\secret.jpg")

    def test_symlink_outside_cache_is_rejected_when_supported(self):
        outside = os.path.join(self.temp_dir.name, "outside.jpg")
        link = os.path.join(self.anime_dir, "linked_char.jpg")
        with open(outside, "wb") as handle:
            handle.write(b"secret")

        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink unavailable: {error}")

        self.assert_rejected("linked_char.jpg")

    def test_normal_unicode_filename_is_served(self):
        filename = "Aoi Yuuki (悠木 碧)_va.jpg"
        self.write_character_file(filename, b"unicode")

        response = self.call_character_img(filename)

        self.assertEqual(response.status_code, 200)
        response.close()


class EpisodeNumberingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.token = "test-token"

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "SEIYUU_CACHE": main.SEIYUU_CACHE,
            "VLC_PATH": main.VLC_PATH,
            "get_cached_anilist_info": main.get_cached_anilist_info,
            "get_episode_cache": main.get_episode_cache,
            "generate_subtitle_vtt_result": main.generate_subtitle_vtt_result,
            "TESTING": main.app.config.get("TESTING"),
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.POSTER_CACHE = os.path.join(self.cache, "posters")
        main.BANNER_CACHE = os.path.join(self.cache, "banners")
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.METADATA_CACHE = os.path.join(self.cache, "metadata")
        main.CHARACTER_CACHE = os.path.join(self.cache, "characters")
        main.EPISODE_CACHE = os.path.join(self.cache, "episodes")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.SEIYUU_CACHE = os.path.join(self.cache, "seiyuu")
        main.VLC_PATH = ""
        main.get_cached_anilist_info = lambda _name: {}
        main.get_episode_cache = lambda *_args, **_kwargs: {
            "duration": "24 min",
            "resolution": "1080p",
        }
        main.app.config["TESTING"] = True

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "lan_access_enabled": True,
            "action_token": self.token,
        })
        os.makedirs(self.library, exist_ok=True)
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()
        self.client = main.app.test_client()

    def tearDown(self):
        for name, value in self.originals.items():
            if name == "TESTING":
                main.app.config["TESTING"] = value
            else:
                setattr(main, name, value)
        main.apply_settings(main.load_settings())

    def url_for(self, endpoint, **values):
        with main.app.test_request_context():
            return main.url_for(endpoint, **values)

    def local_request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-AniBase-Action-Token", self.token)
        return self.client.open(
            path,
            method=method,
            headers=headers,
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            **kwargs,
        )

    def make_anime(self, anime_name, filenames):
        anime_dir = os.path.join(self.library, anime_name)
        os.makedirs(anime_dir, exist_ok=True)
        for filename in filenames:
            with open(os.path.join(anime_dir, filename), "wb") as handle:
                handle.write(b"video")
        return anime_dir

    def test_episode_parser_uses_actual_filename_number(self):
        cases = {
            "Anime - 01.mkv": 1,
            "Anime - 13.mkv": 13,
            "Anime - 14 [1080p].mkv": 14,
            "[Group] Anime - 07 [1080p].mkv": 7,
            "Anime S01E05.mkv": 5,
            "Anime EP12.mkv": 12,
            "Anime Episode 14.mkv": 14,
            "Anime Episode #01.mkv": 1,
            "Anime 05 [HEVC].mkv": 5,
            "Anime - 5.5.mkv": 5.5,
            "86 - 07.mkv": 7,
            "100-man no Inochi no Ue ni Ore wa Tatteiru - 12.mkv": 12,
            "2.5-jigen no Ririsa - 08.mkv": 8,
            "Kuramanime-PRCR_ALT20-01-720p.mkv": 1,
            "Kuramanime-PRCR_ALT20-02-720p.mp4": 2,
            "Kuramanime-PRCR_ALT20-05-1080p.mp4": 5,
            "Kuramanime-PRCR_ALT20-06-720p.mp4": 6,
        }

        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(main.get_episode_number(filename), expected)

    def test_episode_sorting_and_unparsed_fallback(self):
        filenames = [
            "No Number.mkv",
            "Anime - 10.mkv",
            "Anime - 01.mkv",
            "Anime - 02.mkv",
        ]

        sorted_filenames = sorted(filenames, key=main.get_episode_sort_key)

        self.assertEqual(sorted_filenames[:3], [
            "Anime - 01.mkv",
            "Anime - 02.mkv",
            "Anime - 10.mkv",
        ])
        self.assertEqual(sorted_filenames[-1], "No Number.mkv")
        self.assertEqual(main.get_episode_number("No Number.mkv"), 0)
        self.assertEqual(main.get_episode_number("Anime 2023 [1080p].mkv"), 0)
        self.assertEqual(main.get_episode_number("Anime 1080p.mkv"), 0)

    def test_anime_detail_preserves_13_14_episode_numbers_and_count(self):
        self.make_anime("Late Start Anime", [
            "Late Start Anime - 13.mkv",
            "Late Start Anime - 14.mkv",
        ])

        main.sync_all_library("episode numbering test sync")
        response = self.client.get(self.url_for("anime_detail", anime_name="Late Start Anime"))
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Episode 13", body)
        self.assertIn("Episode 14", body)
        self.assertNotIn("Episode 1</strong>", body)
        self.assertNotIn("Episode 2</strong>", body)

        with main.db_connection() as conn:
            count = conn.execute(
                "SELECT episodes FROM anime_library WHERE name = ?",
                ("Late Start Anime",),
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_anime_detail_integrates_season_selector_and_selected_episodes(self):
        anime_dir = os.path.join(self.library, "Seasonal Anime")
        for season_name, filename in (("Season 1", "Episode 01.mkv"), ("Season 2", "Episode 13.mkv")):
            season_dir = os.path.join(anime_dir, season_name)
            os.makedirs(season_dir, exist_ok=True)
            with open(os.path.join(season_dir, filename), "wb") as handle:
                handle.write(b"video")

        def season_metadata(name):
            return {
                "title": name,
                "poster": f"https://img.example/{quote(name)}.jpg",
                "banner": f"https://img.example/{quote(name)}-banner.jpg",
                "format": "TV",
                "genres": [],
                "characters": [{
                    "name": "Season Character",
                    "role": "MAIN",
                    "image_local": "Season Character_char.jpg",
                    "va_name": None,
                }],
                "recommendations": [],
                "relations": [],
            }

        main.get_cached_anilist_info = season_metadata
        response = self.local_request("GET", "/anime/Seasonal%20Anime?season=Season%202")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="season-switcher"', body)
        self.assertIn('class="season-switcher-tab active"', body)
        self.assertIn("loadAnimeSeason(seasonLink.href)", body)
        self.assertIn("season=Season+1", body)
        self.assertIn("season=Season+2", body)
        self.assertIn("<span>1</span>", body)
        self.assertIn("<span>2</span>", body)
        self.assertIn("Episode 13", body)
        self.assertNotIn("Episode 1</strong>", body)
        self.assertIn("Season%202.jpg", body)
        self.assertIn("Season%202-banner.jpg", body)
        self.assertIn(
            "/character_img/Seasonal%20Anime%20Season%202/Season%20Character_char.jpg",
            body,
        )

    def test_legacy_season_routes_redirect_to_integrated_anime_detail(self):
        anime_dir = os.path.join(self.library, "Seasonal Anime", "Season 2")
        os.makedirs(anime_dir, exist_ok=True)
        with open(os.path.join(anime_dir, "Episode 01.mkv"), "wb") as handle:
            handle.write(b"video")

        list_response = self.local_request("GET", "/anime/Seasonal%20Anime/seasons")
        detail_response = self.local_request("GET", "/anime/Seasonal%20Anime/Season%202")

        self.assertEqual(list_response.status_code, 302)
        self.assertTrue(list_response.headers["Location"].endswith("/anime/Seasonal%20Anime"))
        self.assertEqual(detail_response.status_code, 302)
        self.assertIn("season=Season+2", detail_response.headers["Location"])

    def test_home_library_refresh_discovers_new_anime_without_waiting_for_metadata(self):
        self.make_anime("Newly Added Anime", ["Episode 01.mkv"])

        original_thread = main.threading.Thread

        class DeferredThread:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            def start(self):
                return None

        main.threading.Thread = DeferredThread
        try:
            response = self.local_request("POST", "/api/library/refresh")
        finally:
            main.threading.Thread = original_thread

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["anime_count"], 1)
        with main.db_connection() as conn:
            row = conn.execute(
                "SELECT name, episodes FROM anime_library WHERE name = ?",
                ("Newly Added Anime",),
            ).fetchone()
        self.assertEqual(row, ("Newly Added Anime", 1))

    def test_home_library_refresh_requires_action_token(self):
        response = self.client.post(
            "/api/library/refresh",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 403)

    def test_player_prev_next_progress_and_routes_use_actual_gap_episode(self):
        self.make_anime("Gap Anime", [
            "Gap Anime - 05.mkv",
            "Gap Anime - 07.mkv",
        ])

        response = self.client.get(self.url_for(
            "player",
            anime_name="Gap Anime",
            episode="Gap Anime - 05.mkv",
        ))
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Episode 5 (1 of 2 available)", body)
        self.assertIn('data-episode-num="5"', body)
        self.assertIn('data-episode-num="7"', body)
        self.assertIn('data-list-position="1"', body)
        self.assertIn('data-list-position="2"', body)
        self.assertIn("Gap%20Anime%20-%2007.mkv", body)
        self.assertNotIn("Episode 6", body)

        response = self.client.get(self.url_for(
            "player",
            anime_name="Gap Anime",
            episode="Gap Anime - 07.mkv",
        ))
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Episode 7 (2 of 2 available)", body)
        self.assertIn("Gap%20Anime%20-%2005.mkv", body)
        self.assertNotIn("Episode 6", body)

        progress_response = self.local_request(
            "POST",
            "/update_progress",
            json={
                "anime_name": "Gap Anime",
                "episode": "Gap Anime - 07.mkv",
                "episode_num": 7,
                "time_str": "00:01 / 24:00",
                "last_seconds": 1,
                "duration": 1440,
            },
        )
        self.assertEqual(progress_response.status_code, 200)
        history = main.load_history_data()
        self.assertEqual(
            history[main.get_watch_history_key("Gap Anime", "Gap Anime - 07.mkv")]["episode_num"],
            7,
        )

        stream_response = self.client.get(self.url_for(
            "stream_video",
            anime_name="Gap Anime",
            episode="Gap Anime - 07.mkv",
        ))
        self.assertEqual(stream_response.status_code, 200)
        stream_response.close()

        captured = {}
        subtitle_path = os.path.join(main.SUBTITLE_CACHE, "gap.vtt")
        os.makedirs(os.path.dirname(subtitle_path), exist_ok=True)
        with open(subtitle_path, "w", encoding="utf-8") as handle:
            handle.write("WEBVTT\n\n")

        def subtitle_result(video_path, _vtt_path):
            captured["basename"] = os.path.basename(video_path)
            return {
                "ok": True,
                "path": subtitle_path,
                "status": "cached",
                "message": "ok",
            }

        main.generate_subtitle_vtt_result = subtitle_result
        subtitle_response = self.client.get(self.url_for(
            "get_subtitle",
            anime_name="Gap Anime",
            episode="Gap Anime - 07.mkv",
        ))
        self.assertEqual(subtitle_response.status_code, 200)
        subtitle_response.close()
        self.assertEqual(captured["basename"], "Gap Anime - 07.mkv")

        play_response = self.local_request(
            "POST",
            self.url_for(
                "play_episode",
                anime_name="Gap Anime",
                episode="Gap Anime - 07.mkv",
            ),
        )
        self.assertEqual(play_response.status_code, 400)
        self.assertEqual(play_response.get_json()["error"], "vlc_not_configured")


class InternalUrlEncodingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.anime_name = "Anime #100% + A&B (試験)"
        self.episode = "Episode #01 100% + A&B (試験).mkv"
        self.anime_dir = os.path.join(self.library, self.anime_name)
        self.thumb_path = os.path.join(self.cache, "thumb.jpg")

        os.makedirs(self.anime_dir, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.anime_dir, self.episode), "wb") as handle:
            handle.write(b"video")
        with open(self.thumb_path, "wb") as handle:
            handle.write(b"thumb")

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "SEIYUU_CACHE": main.SEIYUU_CACHE,
            "get_cached_anilist_info": main.get_cached_anilist_info,
            "get_episode_cache": main.get_episode_cache,
            "get_thumbnail": main.get_thumbnail,
            "get_thumbnail_result": main.get_thumbnail_result,
            "VLC_PATH": main.VLC_PATH,
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.POSTER_CACHE = os.path.join(self.cache, "posters")
        main.BANNER_CACHE = os.path.join(self.cache, "banners")
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.METADATA_CACHE = os.path.join(self.cache, "metadata")
        main.CHARACTER_CACHE = os.path.join(self.cache, "characters")
        main.EPISODE_CACHE = os.path.join(self.cache, "episodes")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.SEIYUU_CACHE = os.path.join(self.cache, "seiyuu")
        main.get_cached_anilist_info = lambda _name: {}
        main.get_episode_cache = lambda *_args, **_kwargs: {
            "duration": "24 min",
            "resolution": "1080p",
        }
        main.get_thumbnail = lambda _video_path: self.thumb_path
        main.get_thumbnail_result = lambda _video_path: {
            "ok": True,
            "path": self.thumb_path,
            "status": "cached",
            "message": "test thumbnail",
        }

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "lan_access_enabled": True,
            "action_token": "test-token",
        })
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()
        self.client = main.app.test_client()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(main, name, value)
        main.apply_settings(main.load_settings())

    def url_for(self, endpoint, **values):
        with main.app.test_request_context():
            return main.url_for(endpoint, **values)

    def local_request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-AniBase-Action-Token", "test-token")
        return self.client.open(
            path,
            method=method,
            headers=headers,
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            **kwargs,
        )

    def assert_encoded_url(self, url):
        self.assertIn("Anime%20%23100%25%20", url)
        self.assertIn("A&B", url)
        self.assertIn("Episode%20%2301%20100%25%20", url)
        self.assertNotIn("%2520", url)
        self.assertNotIn("%2523", url)
        self.assertNotIn("%2525", url)
        self.assertNotIn("?", url)
        self.assertNotIn("#", url)

    def test_url_for_encodes_special_path_segments_once(self):
        for endpoint in ("player", "stream_video", "get_subtitle", "thumbnail", "play_episode"):
            url = self.url_for(endpoint, anime_name=self.anime_name, episode=self.episode)
            self.assert_encoded_url(url)

        generated = self.url_for(
            "player",
            anime_name="Anime ? # % & + (試験)",
            episode="Episode ? # % & + (試験).mkv",
        )

        self.assertIn("%3F", generated)
        self.assertIn("%23", generated)
        self.assertIn("%25", generated)
        self.assertIn("&", generated)
        self.assertNotIn("%253F", generated)
        self.assertNotIn("%2523", generated)
        self.assertNotIn("%2525", generated)
        self.assertNotIn("?", generated)
        self.assertNotIn("#", generated)

    def test_player_html_contains_encoded_media_urls_without_truncation(self):
        url = self.url_for("player", anime_name=self.anime_name, episode=self.episode)
        response = self.local_request("GET", url)
        body = response.get_data(as_text=True)
        decoded_body = html.unescape(body)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.url_for("stream_video", anime_name=self.anime_name, episode=self.episode), decoded_body)
        self.assertIn(self.url_for("get_subtitle", anime_name=self.anime_name, episode=self.episode), decoded_body)
        self.assertIn(self.url_for("thumbnail", anime_name=self.anime_name, episode=self.episode), decoded_body)
        self.assertIn("/player/__ANIME__/__EPISODE__", decoded_body)
        self.assertIn("/stream/__ANIME__/__EPISODE__", decoded_body)
        self.assertNotIn("%2520", decoded_body)
        self.assertNotIn("%2523", decoded_body)

    def test_media_routes_accept_encoded_internal_urls(self):
        stream_response = self.local_request(
            "GET",
            self.url_for("stream_video", anime_name=self.anime_name, episode=self.episode),
        )
        self.assertEqual(stream_response.status_code, 200)
        stream_response.close()

        subtitle_path = main.get_subtitle_vtt_path(self.anime_name, self.episode)
        os.makedirs(os.path.dirname(subtitle_path), exist_ok=True)
        with open(subtitle_path, "w", encoding="utf-8") as handle:
            handle.write("WEBVTT\n\n")

        subtitle_response = self.local_request(
            "GET",
            self.url_for("get_subtitle", anime_name=self.anime_name, episode=self.episode),
        )
        self.assertEqual(subtitle_response.status_code, 200)
        subtitle_response.close()

        thumbnail_response = self.local_request(
            "GET",
            self.url_for("thumbnail", anime_name=self.anime_name, episode=self.episode),
        )
        self.assertEqual(thumbnail_response.status_code, 200)
        thumbnail_response.close()

    def test_poster_and_character_urls_with_normal_special_names_work(self):
        os.makedirs(main.POSTER_CACHE, exist_ok=True)
        poster_path = os.path.join(main.POSTER_CACHE, f"{main.safe_cache_name(self.anime_name)}.jpg")
        with open(poster_path, "wb") as handle:
            handle.write(b"poster")

        poster_response = self.local_request(
            "GET",
            self.url_for("poster", anime_name=self.anime_name),
        )
        self.assertEqual(poster_response.status_code, 200)
        poster_response.close()

        filename = "Alice #100% + (悠木 碧).jpg"
        character_dir = os.path.join(main.CHARACTER_CACHE, main.safe_cache_name(self.anime_name))
        os.makedirs(character_dir, exist_ok=True)
        with open(os.path.join(character_dir, filename), "wb") as handle:
            handle.write(b"character")

        character_response = self.local_request(
            "GET",
            self.url_for("character_img", anime_name=self.anime_name, filename=filename),
        )
        self.assertEqual(character_response.status_code, 200)
        character_response.close()

    def test_encoded_traversal_still_does_not_leave_media_root(self):
        outside_path = os.path.join(self.root, "secret.mkv")
        with open(outside_path, "wb") as handle:
            handle.write(b"secret")

        encoded_anime = quote(self.anime_name, safe="")
        response = self.local_request(
            "GET",
            f"/stream/{encoded_anime}/..%2Fsecret.mkv",
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.root, response.get_data(as_text=True))

    def test_play_endpoint_resolves_encoded_url_before_vlc_validation(self):
        response = self.local_request(
            "POST",
            self.url_for("play_episode", anime_name=self.anime_name, episode=self.episode),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "vlc_not_configured")

    def test_progress_endpoint_preserves_special_episode_key(self):
        response = self.local_request(
            "POST",
            "/api/watch-status/progress",
            json={
                "anime_name": self.anime_name,
                "episode": self.episode,
                "progress": 55,
                "duration": 1440,
                "current_seconds": 792,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            main.get_episode_watch_status(self.anime_name, self.episode).get("progress"),
            55,
        )


class SeiyuuPageTests(unittest.TestCase):
    def setUp(self):
        self.original_fetch = main.fetch_anilist_seiyuu_detail
        self.original_get_anime = main.get_anime
        self.original_cached_metadata = main.get_cached_metadata_only
        self.original_setup_complete = main.is_setup_complete
        main.is_setup_complete = lambda *_args, **_kwargs: True
        main.get_cached_metadata_only = lambda _name: {}
        self.client = main.app.test_client()

    def tearDown(self):
        main.fetch_anilist_seiyuu_detail = self.original_fetch
        main.get_anime = self.original_get_anime
        main.get_cached_metadata_only = self.original_cached_metadata
        main.is_setup_complete = self.original_setup_complete

    def make_profile(self, roles=None, description=None, **overrides):
        profile = {
            "staff_id": 1,
            "name_full": "Test Voice Actor",
            "name_native": "テスト",
            "image": None,
            "description": description,
            "date_of_birth": None,
            "age": None,
            "gender": None,
            "blood_type": None,
            "home_town": None,
            "language": None,
            "site_url": "https://anilist.co/staff/1",
            "voice_roles": roles or [],
            "source": "AniList",
        }
        profile.update(overrides)
        return profile

    def request_profile(self, profile, local_anime=None):
        main.fetch_anilist_seiyuu_detail = lambda *_args, **_kwargs: (profile, None)
        main.get_anime = lambda: local_anime or []
        return self.client.get("/seiyuu/1", environ_base={"REMOTE_ADDR": "127.0.0.1"})

    def test_seiyuu_route_renders_incomplete_data_and_empty_state(self):
        response = self.request_profile(self.make_profile())
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Test Voice Actor", body)
        self.assertIn("No related anime in your library", body)
        self.assertIn("No other roles available", body)

    def test_seiyuu_bio_is_structured_and_escaped(self):
        description = (
            "__Height:__ 160 cm\n\n"
            "[Twitter](https://twitter.com/example)\n\n"
            "~!Visible spoiler!~ <script>alert(1)</script>"
        )
        response = self.request_profile(self.make_profile(description=description))
        body = response.get_data(as_text=True)

        self.assertNotIn("__Height:__", body)
        self.assertNotIn("~!", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("<strong>Height:</strong>", body)
        self.assertIn("Twitter", body)
        self.assertIn('target="_blank" rel="noopener noreferrer"', body)

    def test_only_local_roles_link_to_local_anime_route(self):
        roles = [
            {"media_title": "Local Show", "media_title_romaji": "Local Show", "character_name": "Local Character", "character_image": "https://img.example/local-character.jpg", "media_poster": "https://img.example/local-poster.jpg"},
            {"media_title": "Remote Show", "media_title_romaji": "Remote Show", "character_name": "Remote Character", "character_image": "https://img.example/remote-character.jpg", "media_poster": "https://img.example/remote-poster.jpg"},
        ]
        response = self.request_profile(
            self.make_profile(roles=roles),
            local_anime=[{"name": "Local Show"}],
        )
        body = response.get_data(as_text=True)

        self.assertEqual(body.count('class="sy-role-action"'), 1)
        self.assertIn("Local Character", body)
        self.assertIn("Remote Character", body)
        self.assertNotIn('href="/anime/Remote', body)

    def test_role_cards_render_character_images_instead_of_anime_posters(self):
        roles = [{
            "media_title": "Example Anime",
            "media_title_romaji": "Example Anime",
            "character_name": "Example Character",
            "character_image": "https://img.example/character.jpg",
            "media_poster": "https://img.example/poster.jpg",
        }]

        response = self.request_profile(self.make_profile(roles=roles))
        body = response.get_data(as_text=True)
        role_card = body.split('<article class="sy-role">', 1)[1].split('</article>', 1)[0]

        self.assertIn('class="sy-role-character"', role_card)
        self.assertIn('src="https://img.example/character.jpg"', role_card)
        self.assertNotIn('src="https://img.example/poster.jpg"', role_card)
        self.assertLess(role_card.index("Example Character"), role_card.index("Example Anime"))

    def test_role_card_without_character_image_uses_character_initial(self):
        roles = [{
            "media_title": "Example Anime",
            "media_title_romaji": "Example Anime",
            "character_name": "Mika",
            "character_image": None,
            "media_poster": "https://img.example/poster.jpg",
        }]

        response = self.request_profile(self.make_profile(roles=roles))
        body = response.get_data(as_text=True)
        role_card = body.split('<article class="sy-role">', 1)[1].split('</article>', 1)[0]

        self.assertIn('<div class="sy-role-fallback" aria-hidden="true">M</div>', role_card)
        self.assertNotIn('src="https://img.example/poster.jpg"', role_card)


class MediaDependencyDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.anime_name = "Dependency Test"
        self.episode = "Episode 01.mkv"
        self.anime_dir = os.path.join(self.library, self.anime_name)

        os.makedirs(self.anime_dir, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.anime_dir, self.episode), "wb") as handle:
            handle.write(b"video")

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "SEIYUU_CACHE": main.SEIYUU_CACHE,
            "VLC_PATH": main.VLC_PATH,
            "get_cached_anilist_info": main.get_cached_anilist_info,
            "get_episode_cache": main.get_episode_cache,
            "shutil_which": main.shutil.which,
            "subprocess_run": main.subprocess.run,
            "subprocess_popen": main.subprocess.Popen,
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.POSTER_CACHE = os.path.join(self.cache, "posters")
        main.BANNER_CACHE = os.path.join(self.cache, "banners")
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.METADATA_CACHE = os.path.join(self.cache, "metadata")
        main.CHARACTER_CACHE = os.path.join(self.cache, "characters")
        main.EPISODE_CACHE = os.path.join(self.cache, "episodes")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.SEIYUU_CACHE = os.path.join(self.cache, "seiyuu")
        main.VLC_PATH = ""
        main.get_cached_anilist_info = lambda _name: {}
        main.get_episode_cache = lambda *_args, **_kwargs: {
            "duration": "24 min",
            "resolution": "1080p",
        }
        with main.MEDIA_DIAGNOSTIC_CACHE_LOCK:
            main.MEDIA_DIAGNOSTIC_CACHE.clear()

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "lan_access_enabled": True,
            "action_token": "test-token",
        })
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()
        self.client = main.app.test_client()

    def tearDown(self):
        for name, value in self.originals.items():
            if name == "shutil_which":
                main.shutil.which = value
            elif name == "subprocess_run":
                main.subprocess.run = value
            elif name == "subprocess_popen":
                main.subprocess.Popen = value
            else:
                setattr(main, name, value)
        with main.MEDIA_DIAGNOSTIC_CACHE_LOCK:
            main.MEDIA_DIAGNOSTIC_CACHE.clear()
        main.apply_settings(main.load_settings())

    def local_request(self, method, path, **kwargs):
        return self.client.open(
            path,
            method=method,
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            **kwargs,
        )

    def lan_request(self, method, path, **kwargs):
        return self.client.open(
            path,
            method=method,
            environ_base={"REMOTE_ADDR": "192.168.1.20"},
            **kwargs,
        )

    def set_tool_probe(self, paths=None, run_result=None, run_error=None):
        paths = paths or {}

        def fake_which(name):
            return paths.get(name)

        def fake_run(args, **_kwargs):
            if run_error:
                raise run_error
            return run_result or main.subprocess.CompletedProcess(args, 0, stdout="tool version 1.0\n", stderr="")

        main.shutil.which = fake_which
        main.subprocess.run = fake_run
        with main.MEDIA_DIAGNOSTIC_CACHE_LOCK:
            main.MEDIA_DIAGNOSTIC_CACHE.clear()

    def make_file(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"exe")
        return path

    def settings_with_vlc(self, vlc_path):
        settings = main.load_settings()
        settings["vlc_path"] = vlc_path
        main.save_settings(settings)
        main.apply_settings(settings)
        with main.MEDIA_DIAGNOSTIC_CACHE_LOCK:
            main.MEDIA_DIAGNOSTIC_CACHE.clear()
        return settings

    def test_ffmpeg_available_returns_version(self):
        ffmpeg = self.make_file("bin/ffmpeg.exe")
        self.set_tool_probe({"ffmpeg": ffmpeg})

        result = main.diagnose_path_executable("FFmpeg", "ffmpeg")

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["version"], "tool version 1.0")

    def test_ffmpeg_not_found(self):
        self.set_tool_probe({})

        result = main.diagnose_path_executable("FFmpeg", "ffmpeg")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "not_found")

    def test_ffmpeg_timeout(self):
        ffmpeg = self.make_file("bin/ffmpeg.exe")
        self.set_tool_probe(
            {"ffmpeg": ffmpeg},
            run_error=main.subprocess.TimeoutExpired([ffmpeg, "-version"], 1),
        )

        result = main.diagnose_path_executable("FFmpeg", "ffmpeg")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "error")
        self.assertIn("timed out", result["message"])

    def test_ffmpeg_non_zero_exit(self):
        ffmpeg = self.make_file("bin/ffmpeg.exe")
        self.set_tool_probe(
            {"ffmpeg": ffmpeg},
            run_result=main.subprocess.CompletedProcess(
                [ffmpeg, "-version"],
                2,
                stdout="ffmpeg version broken\n",
                stderr="error",
            ),
        )

        result = main.diagnose_path_executable("FFmpeg", "ffmpeg")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["version"], "ffmpeg version broken")

    def test_ffprobe_available(self):
        ffprobe = self.make_file("bin/ffprobe.exe")
        self.set_tool_probe({"ffprobe": ffprobe})

        result = main.diagnose_path_executable("FFprobe", "ffprobe")

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "tool version 1.0")

    def test_ffprobe_not_found(self):
        self.set_tool_probe({})

        result = main.diagnose_path_executable("FFprobe", "ffprobe")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "not_found")

    def test_vlc_not_configured(self):
        result = main.diagnose_vlc_path("")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "not_configured")

    def test_vlc_path_valid(self):
        path = self.make_file("VLC/vlc.exe")

        result = main.diagnose_vlc_path(path)

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "available")

    def test_vlc_path_not_found(self):
        result = main.diagnose_vlc_path(os.path.join(self.root, "missing-vlc.exe"))

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "path_invalid")

    def test_vlc_path_folder_is_invalid(self):
        folder = os.path.join(self.root, "VLC Folder")
        os.makedirs(folder, exist_ok=True)

        result = main.diagnose_vlc_path(folder)

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "path_invalid")

    def test_vlc_path_with_spaces_and_unicode(self):
        path = self.make_file("VLC 試験/vlc player.exe")

        result = main.diagnose_vlc_path(path)

        self.assertTrue(result["available"])
        self.assertEqual(result["path"], os.path.abspath(path))

    def test_permission_error_is_handled(self):
        ffmpeg = self.make_file("bin/ffmpeg.exe")
        self.set_tool_probe({"ffmpeg": ffmpeg}, run_error=PermissionError("denied"))

        result = main.diagnose_path_executable("FFmpeg", "ffmpeg")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "error")
        self.assertNotIn("denied", result["message"])

    def test_settings_status_hides_media_tools_and_shows_discord(self):
        ffmpeg = self.make_file("bin/ffmpeg.exe")
        ffprobe = self.make_file("bin/ffprobe.exe")
        self.set_tool_probe({"ffmpeg": ffmpeg, "ffprobe": ffprobe})

        response = self.local_request("GET", "/settings")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("FFmpeg", body)
        self.assertNotIn("FFprobe", body)
        self.assertIn("Discord", body)

    def test_settings_diagnostics_rejected_from_lan(self):
        response = self.lan_request("GET", "/settings")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.root, body)

    def test_spoofed_forwarded_for_does_not_bypass_diagnostics_host_only(self):
        response = self.lan_request(
            "GET",
            "/settings",
            headers={
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_diagnostics_requires_action_token(self):
        response = self.local_request("POST", "/settings/media-diagnostics/check")

        self.assertEqual(response.status_code, 403)

    def test_refresh_diagnostics_with_action_token_redirects(self):
        response = self.local_request(
            "POST",
            "/settings/media-diagnostics/check",
            data={"action_token": "test-token"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("diagnostics_refreshed=1", response.headers["Location"])

    def test_missing_ffmpeg_does_not_break_anime_or_player_pages(self):
        self.set_tool_probe({})

        with main.app.test_request_context():
            anime_url = main.url_for("anime_detail", anime_name=self.anime_name)
            player_url = main.url_for("player", anime_name=self.anime_name, episode=self.episode)

        anime_response = self.local_request("GET", anime_url)
        player_response = self.local_request("GET", player_url)

        self.assertEqual(anime_response.status_code, 200)
        self.assertEqual(player_response.status_code, 200)

    def test_missing_vlc_does_not_break_web_player(self):
        main.VLC_PATH = ""

        with main.app.test_request_context():
            player_url = main.url_for("player", anime_name=self.anime_name, episode=self.episode)

        response = self.local_request("GET", player_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Media Player Not Set", response.get_data(as_text=True))


class HttpStatusConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.movies = os.path.join(self.root, "movies")
        self.anime_name = "Status Test"
        self.episode = "Episode 01.mkv"
        self.anime_dir = os.path.join(self.library, self.anime_name)

        os.makedirs(self.anime_dir, exist_ok=True)
        os.makedirs(self.movies, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.anime_dir, self.episode), "wb") as handle:
            handle.write(b"video")

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "SEIYUU_CACHE": main.SEIYUU_CACHE,
            "VLC_PATH": main.VLC_PATH,
            "get_cached_anilist_info": main.get_cached_anilist_info,
            "get_episode_cache": main.get_episode_cache,
            "get_thumbnail": main.get_thumbnail,
            "get_thumbnail_result": main.get_thumbnail_result,
            "generate_subtitle_vtt": main.generate_subtitle_vtt,
            "generate_subtitle_vtt_result": main.generate_subtitle_vtt_result,
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = self.movies
        main.POSTER_CACHE = os.path.join(self.cache, "posters")
        main.BANNER_CACHE = os.path.join(self.cache, "banners")
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.METADATA_CACHE = os.path.join(self.cache, "metadata")
        main.CHARACTER_CACHE = os.path.join(self.cache, "characters")
        main.EPISODE_CACHE = os.path.join(self.cache, "episodes")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.SEIYUU_CACHE = os.path.join(self.cache, "seiyuu")
        main.VLC_PATH = ""
        main.get_cached_anilist_info = lambda _name: {}
        main.get_episode_cache = lambda *_args, **_kwargs: {
            "duration": "24 min",
            "resolution": "1080p",
        }
        main.get_thumbnail = lambda _video_path: None
        main.get_thumbnail_result = lambda _video_path: {
            "ok": False,
            "path": "",
            "status": "failed",
            "message": "test thumbnail unavailable",
        }
        main.generate_subtitle_vtt = lambda _video_path, _vtt_path: False
        main.generate_subtitle_vtt_result = lambda _video_path, _vtt_path: {
            "ok": False,
            "path": "",
            "status": "failed",
            "message": "test subtitle unavailable",
        }

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "movie_path": self.movies,
            "lan_access_enabled": True,
            "action_token": "test-token",
        })
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()
        self.client = main.app.test_client()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(main, name, value)
        main.apply_settings(main.load_settings())
        if main.LIBRARY_SYNC_LOCK.locked():
            main.LIBRARY_SYNC_LOCK.release()

    def url_for(self, endpoint, **values):
        with main.app.test_request_context():
            return main.url_for(endpoint, **values)

    def request(self, method, path, remote_addr="127.0.0.1", token="test-token", **kwargs):
        headers = kwargs.pop("headers", {})
        if token is not None:
            headers.setdefault("X-AniBase-Action-Token", token)
        return self.client.open(
            path,
            method=method,
            headers=headers,
            environ_base={"REMOTE_ADDR": remote_addr},
            **kwargs,
        )

    def assert_safe_error_body(self, response):
        body = response.get_data(as_text=True)
        self.assertNotIn(self.root, body)
        self.assertNotIn("Traceback", body)

    def test_anime_not_found_returns_404(self):
        response = self.request("GET", "/anime/Missing Anime")

        self.assertEqual(response.status_code, 404)
        self.assert_safe_error_body(response)

    def test_movie_not_found_returns_404(self):
        response = self.request("GET", "/movie/Missing.mkv")

        self.assertEqual(response.status_code, 404)
        self.assert_safe_error_body(response)

    def test_episode_not_found_returns_404(self):
        response = self.request(
            "GET",
            self.url_for("player", anime_name=self.anime_name, episode="Missing.mkv"),
        )

        self.assertEqual(response.status_code, 404)
        self.assert_safe_error_body(response)

    def test_stream_file_not_found_returns_404(self):
        response = self.request(
            "GET",
            self.url_for("stream_video", anime_name=self.anime_name, episode="Missing.mkv"),
        )

        self.assertEqual(response.status_code, 404)
        self.assert_safe_error_body(response)

    def test_subtitle_unavailable_returns_404(self):
        response = self.request(
            "GET",
            self.url_for("get_subtitle", anime_name=self.anime_name, episode=self.episode),
        )

        self.assertEqual(response.status_code, 404)

    def test_thumbnail_unavailable_returns_404(self):
        response = self.request(
            "GET",
            self.url_for("thumbnail", anime_name=self.anime_name, episode=self.episode),
        )

        self.assertEqual(response.status_code, 404)

    def test_character_image_unavailable_returns_404(self):
        response = self.request(
            "GET",
            self.url_for("character_img", anime_name=self.anime_name, filename="missing.jpg"),
        )

        self.assertEqual(response.status_code, 404)

    def test_invalid_json_progress_request_returns_400(self):
        response = self.request(
            "POST",
            "/update_progress",
            data="{",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_json")

    def test_progress_missing_required_fields_returns_400(self):
        response = self.request(
            "POST",
            "/update_progress",
            json={"anime_name": self.anime_name},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "missing_progress_fields")

    def test_invalid_watch_status_returns_400(self):
        response = self.request(
            "POST",
            "/api/watch-status/progress",
            json={
                "anime_name": self.anime_name,
                "episode": self.episode,
                "progress": "not-a-number",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_watch_status")

    def test_sync_conflict_returns_409(self):
        self.assertTrue(main.LIBRARY_SYNC_LOCK.acquire(blocking=False))
        try:
            response = self.request("POST", "/setup/sync")
        finally:
            if main.LIBRARY_SYNC_LOCK.locked():
                main.LIBRARY_SYNC_LOCK.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "sync_in_progress")

    def test_invalid_vlc_path_returns_controlled_json(self):
        settings = main.load_settings()
        settings["vlc_path"] = os.path.join(self.root, "missing-vlc.exe")
        main.save_settings(settings)
        main.apply_settings(settings)

        response = self.request(
            "POST",
            self.url_for("play_episode", anime_name=self.anime_name, episode=self.episode),
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["error"], "vlc_not_found")
        self.assertNotIn(self.root, data["message"])

    def test_screenshot_payload_too_large_returns_413(self):
        response = self.request(
            "POST",
            "/screenshot",
            json={"image": "data:image/png;base64," + ("A" * (main.MAX_SCREENSHOT_DATA_URL_BYTES + 1))},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"], "payload_too_large")

    def test_host_only_route_from_lan_returns_403(self):
        response = self.request("POST", "/settings/cleanup-cache", remote_addr="192.168.1.20")

        self.assertEqual(response.status_code, 403)

    def test_action_token_invalid_returns_403(self):
        response = self.request("POST", "/clear_rpc", token="wrong-token")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "invalid_action_token")

    def test_success_pages_and_media_still_return_200(self):
        anime_response = self.request("GET", self.url_for("anime_detail", anime_name=self.anime_name))
        stream_response = self.request(
            "GET",
            self.url_for("stream_video", anime_name=self.anime_name, episode=self.episode),
        )

        self.assertEqual(anime_response.status_code, 200)
        self.assertEqual(stream_response.status_code, 200)
        stream_response.close()


class MediaGenerationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.anime_name = "Media Generate"
        self.episode = "Episode 01.mkv"
        self.anime_dir = os.path.join(self.library, self.anime_name)

        os.makedirs(self.anime_dir, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        self.video_path = os.path.join(self.anime_dir, self.episode)
        with open(self.video_path, "wb") as handle:
            handle.write(b"video")

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "get_thumbnail_seek_points": main.get_thumbnail_seek_points,
            "subprocess_run": main.subprocess.run,
            "FFMPEG_SEMAPHORE": main.FFMPEG_SEMAPHORE,
            "FFMPEG_FAILURE_CACHE_TTL_SECONDS": main.FFMPEG_FAILURE_CACHE_TTL_SECONDS,
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.get_thumbnail_seek_points = lambda _path: [10]
        main.FFMPEG_SEMAPHORE = threading.BoundedSemaphore(main.FFMPEG_MAX_CONCURRENT_PROCESSES)
        self.clear_generation_state()

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "lan_access_enabled": True,
            "action_token": "test-token",
        })
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()

    def tearDown(self):
        for name, value in self.originals.items():
            if name == "subprocess_run":
                main.subprocess.run = value
            else:
                setattr(main, name, value)
        self.clear_generation_state()
        main.apply_settings(main.load_settings())

    def clear_generation_state(self):
        with main.FFMPEG_MEDIA_LOCKS_GUARD:
            main.FFMPEG_MEDIA_LOCKS.clear()
        with main.FFMPEG_FAILURE_CACHE_LOCK:
            main.FFMPEG_FAILURE_CACHE.clear()

    def make_video(self, filename):
        path = os.path.join(self.anime_dir, filename)
        with open(path, "wb") as handle:
            handle.write(b"video")
        return path

    def fake_ffmpeg(self, calls, content=b"cache", delay=0.05, active=None):
        active = active or {}
        lock = threading.Lock()

        def fake_run(args, **_kwargs):
            output_path = args[-1]
            with lock:
                calls.append(tuple(args))
                active["current"] = active.get("current", 0) + 1
                active["max"] = max(active.get("max", 0), active["current"])
            try:
                time.sleep(delay)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as handle:
                    handle.write(content)
                return main.subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            finally:
                with lock:
                    active["current"] -= 1

        main.subprocess.run = fake_run
        return active

    def test_two_same_thumbnail_requests_run_ffmpeg_once(self):
        calls = []
        self.fake_ffmpeg(calls, b"thumb")
        results = []

        threads = [
            threading.Thread(target=lambda: results.append(main.get_thumbnail_result(self.video_path)))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertFalse(main.FFMPEG_MEDIA_LOCKS)

    def test_two_same_subtitle_requests_run_ffmpeg_once(self):
        calls = []
        self.fake_ffmpeg(calls, b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n")
        vtt_path = main.get_subtitle_vtt_path(self.anime_name, self.episode)
        results = []

        threads = [
            threading.Thread(target=lambda: results.append(main.generate_subtitle_vtt_result(self.video_path, vtt_path)))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertFalse(main.FFMPEG_MEDIA_LOCKS)

    def test_different_media_respects_ffmpeg_semaphore_limit(self):
        calls = []
        active = self.fake_ffmpeg(calls, b"thumb", delay=0.1)
        videos = [self.make_video(f"Episode {index:02d}.mkv") for index in range(1, 5)]
        threads = [
            threading.Thread(target=lambda path=path: main.get_thumbnail_result(path))
            for path in videos
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 4)
        self.assertLessEqual(active["max"], main.FFMPEG_MAX_CONCURRENT_PROCESSES)

    def test_cache_hit_does_not_run_ffmpeg(self):
        thumbnail_path = main.get_thumbnail_cache_path(self.video_path)
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        with open(thumbnail_path, "wb") as handle:
            handle.write(b"cached")
        calls = []
        self.fake_ffmpeg(calls)

        result = main.get_thumbnail_result(self.video_path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "cached")
        self.assertEqual(calls, [])

    def test_empty_cache_file_is_regenerated(self):
        thumbnail_path = main.get_thumbnail_cache_path(self.video_path)
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        open(thumbnail_path, "wb").close()
        calls = []
        self.fake_ffmpeg(calls, b"thumb")

        result = main.get_thumbnail_result(self.video_path)

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        self.assertGreater(os.path.getsize(thumbnail_path), 0)

    def test_timeout_releases_lock_and_semaphore_and_cleans_temp(self):
        def fake_run(args, **_kwargs):
            output_path = args[-1]
            with open(output_path, "wb") as handle:
                handle.write(b"partial")
            raise main.subprocess.TimeoutExpired(args, 1)

        main.subprocess.run = fake_run
        result = main.get_thumbnail_result(self.video_path)

        self.assertEqual(result["status"], "timeout")
        self.assertFalse(main.FFMPEG_MEDIA_LOCKS)
        self.assertFalse([
            name for name in os.listdir(main.THUMBNAIL_CACHE)
            if ".tmp" in name
        ])
        acquired = [main.FFMPEG_SEMAPHORE.acquire(timeout=0.1) for _ in range(main.FFMPEG_MAX_CONCURRENT_PROCESSES)]
        self.assertTrue(all(acquired))
        for _ in acquired:
            main.FFMPEG_SEMAPHORE.release()

    def test_failure_cache_prevents_immediate_retry_and_expires(self):
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(tuple(args))
            return main.subprocess.CompletedProcess(args, 1, stdout="", stderr="no subtitle")

        main.subprocess.run = fake_run
        main.FFMPEG_FAILURE_CACHE_TTL_SECONDS = 0.01

        first = main.get_thumbnail_result(self.video_path)
        second = main.get_thumbnail_result(self.video_path)
        time.sleep(0.03)
        third = main.get_thumbnail_result(self.video_path)

        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertFalse(third["ok"])
        self.assertEqual(len(calls), 2)

    def test_busy_route_returns_503_without_starting_unbounded_work(self):
        acquired = [main.FFMPEG_SEMAPHORE.acquire(timeout=0.1) for _ in range(main.FFMPEG_MAX_CONCURRENT_PROCESSES)]
        self.assertTrue(all(acquired))
        calls = []
        self.fake_ffmpeg(calls, b"thumb")

        try:
            with main.app.test_request_context():
                url = main.url_for("thumbnail", anime_name=self.anime_name, episode=self.episode)
            client = main.app.test_client()
            response = client.get(url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        finally:
            for _ in acquired:
                main.FFMPEG_SEMAPHORE.release()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(calls, [])

    def test_source_missing_returns_404(self):
        result = main.get_thumbnail_result(os.path.join(self.anime_dir, "missing.mkv"))

        self.assertEqual(result["status"], "source_not_found")

    def test_lan_can_load_generated_thumbnail_and_subtitle(self):
        calls = []
        self.fake_ffmpeg(calls, b"thumb")
        client = main.app.test_client()
        with main.app.test_request_context():
            thumbnail_url = main.url_for("thumbnail", anime_name=self.anime_name, episode=self.episode)

        thumbnail_response = client.get(thumbnail_url, environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(thumbnail_response.status_code, 200)
        thumbnail_response.close()

        self.clear_generation_state()
        calls.clear()
        self.fake_ffmpeg(calls, b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n")
        with main.app.test_request_context():
            subtitle_url = main.url_for("get_subtitle", anime_name=self.anime_name, episode=self.episode)

        subtitle_response = client.get(subtitle_url, environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(subtitle_response.status_code, 200)
        subtitle_response.close()


class LanHostOnlyAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.cache = os.path.join(self.root, "cache")
        self.library = os.path.join(self.root, "library")
        self.anime_name = "Demo Anime"
        self.episode = "Episode 01.mkv"
        self.anime_dir = os.path.join(self.library, self.anime_name)
        os.makedirs(self.anime_dir, exist_ok=True)
        with open(os.path.join(self.anime_dir, self.episode), "wb") as handle:
            handle.write(b"video")

        self.originals = {
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "cleanup_orphan_cache": main.cleanup_orphan_cache,
        }

        main.SETTINGS_FILE = os.path.join(self.cache, "settings.json")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.cleanup_orphan_cache = lambda: {
            "removed_files": 0,
            "removed_dirs": 0,
            "removed_watch_entries": 0,
            "skipped": 0,
        }

        settings = main.get_default_settings()
        settings.update({
            "setup_completed": True,
            "library_paths": [self.library],
            "watchlist_path": self.library,
            "lan_access_enabled": True,
            "action_token": "test-token",
        })
        main.save_settings(settings)
        main.apply_settings(settings)
        main.init_db()
        self.client = main.app.test_client()
        self.token = "test-token"

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(main, name, value)
        main.apply_settings(main.load_settings())

    def request(self, method, path, remote_addr, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-AniBase-Action-Token", self.token)
        return self.client.open(
            path,
            method=method,
            headers=headers,
            environ_base={"REMOTE_ADDR": remote_addr},
            **kwargs,
        )

    def test_host_only_accepts_ipv4_loopback_with_valid_token(self):
        response = self.request("POST", "/settings/cleanup-cache", "127.0.0.1")

        self.assertNotEqual(response.status_code, 403)

    def test_host_only_accepts_ipv6_loopback_with_valid_token(self):
        response = self.request("POST", "/settings/cleanup-cache", "::1")

        self.assertNotEqual(response.status_code, 403)

    def test_host_only_accepts_ipv4_mapped_loopback_with_valid_token(self):
        response = self.request("POST", "/settings/cleanup-cache", "::ffff:127.0.0.1")

        self.assertNotEqual(response.status_code, 403)

    def test_host_only_rejects_192_lan(self):
        response = self.request("POST", "/settings/cleanup-cache", "192.168.1.20")

        self.assertEqual(response.status_code, 403)

    def test_host_only_rejects_10_lan(self):
        response = self.request("POST", "/settings/cleanup-cache", "10.0.0.5")

        self.assertEqual(response.status_code, 403)

    def test_host_only_rejects_172_lan(self):
        response = self.request("POST", "/settings/cleanup-cache", "172.16.0.5")

        self.assertEqual(response.status_code, 403)

    def test_spoofed_forwarded_for_does_not_bypass_host_only(self):
        response = self.request(
            "POST",
            "/settings/cleanup-cache",
            "192.168.1.20",
            headers={
                "X-AniBase-Action-Token": self.token,
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_settings_get_from_lan_is_rejected_without_paths_or_token(self):
        response = self.request("GET", "/settings", "192.168.1.20")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.library, body)
        self.assertNotIn(self.token, body)

    def test_settings_post_from_lan_is_rejected_even_with_valid_token(self):
        response = self.request(
            "POST",
            "/settings",
            "192.168.1.20",
            data={"library_paths": self.library, "action_token": self.token},
        )

        self.assertEqual(response.status_code, 403)

    def test_setup_sync_from_lan_is_rejected(self):
        response = self.request("POST", "/setup/sync", "192.168.1.20")

        self.assertEqual(response.status_code, 403)

    def test_folder_picker_from_lan_is_rejected(self):
        response = self.request("GET", "/settings/pick-folder", "192.168.1.20")

        self.assertEqual(response.status_code, 403)

    def test_open_vlc_from_lan_is_rejected(self):
        response = self.request(
            "POST",
            f"/play/{self.anime_name}/{self.episode}",
            "192.168.1.20",
        )

        self.assertEqual(response.status_code, 403)

    def test_screenshot_save_from_lan_is_rejected(self):
        response = self.request(
            "POST",
            "/screenshot",
            "192.168.1.20",
            json={"image": "data:image/png;base64,"},
        )

        self.assertEqual(response.status_code, 403)

    def test_clear_rpc_from_lan_is_rejected(self):
        response = self.request("POST", "/clear_rpc", "192.168.1.20")

        self.assertEqual(response.status_code, 403)

    def test_watch_history_remove_from_lan_is_rejected(self):
        response = self.request(
            "POST",
            "/api/watch-history/remove",
            "192.168.1.20",
            json={"history_key": self.anime_name},
        )

        self.assertEqual(response.status_code, 403)

    def test_progress_endpoint_from_lan_still_works(self):
        response = self.request(
            "POST",
            "/update_progress",
            "192.168.1.20",
            json={
                "anime_name": self.anime_name,
                "episode": self.episode,
                "episode_num": 1,
                "time_str": "00:01 / 24:00",
                "last_seconds": 1,
                "duration": 1440,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.anime_name, main.load_history_data())

    def test_watch_status_progress_from_lan_still_works(self):
        response = self.request(
            "POST",
            "/api/watch-status/progress",
            "192.168.1.20",
            json={
                "anime_name": self.anime_name,
                "episode": self.episode,
                "progress": 50,
                "duration": 1440,
                "current_seconds": 720,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            main.get_episode_watch_status(self.anime_name, self.episode).get("progress"),
            50,
        )

    def test_media_stream_from_lan_still_works(self):
        response = self.request(
            "GET",
            f"/stream/{self.anime_name}/{self.episode}",
            "192.168.1.20",
        )

        self.assertEqual(response.status_code, 200)
        response.close()

    def test_subtitle_from_lan_still_works_when_cached(self):
        vtt_path = main.get_subtitle_vtt_path(self.anime_name, self.episode)
        os.makedirs(os.path.dirname(vtt_path), exist_ok=True)
        with open(vtt_path, "w", encoding="utf-8") as handle:
            handle.write("WEBVTT\n\n")

        response = self.request(
            "GET",
            f"/subtitle/{self.anime_name}/{self.episode}",
            "192.168.1.20",
        )

        self.assertEqual(response.status_code, 200)
        response.close()

    def test_lan_access_disabled_still_blocks_lan(self):
        settings = main.load_settings()
        settings["lan_access_enabled"] = False
        main.save_settings(settings)

        response = self.request("GET", "/", "192.168.1.20")

        self.assertEqual(response.status_code, 403)


class LibrarySyncStaleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.library = os.path.join(self.root, "library")
        self.second_library = os.path.join(self.root, "second-library")
        self.cache = os.path.join(self.root, "cache")

        self.originals = {
            "DB_PATH": main.DB_PATH,
            "ANIME_PATHS": main.ANIME_PATHS,
            "MOVIE_PATH": main.MOVIE_PATH,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "SEIYUU_CACHE": main.SEIYUU_CACHE,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "get_cached_anilist_info": main.get_cached_anilist_info,
        }

        main.DB_PATH = os.path.join(self.cache, "library.db")
        main.ANIME_PATHS = [self.library]
        main.MOVIE_PATH = ""
        main.POSTER_CACHE = os.path.join(self.cache, "posters")
        main.BANNER_CACHE = os.path.join(self.cache, "banners")
        main.THUMBNAIL_CACHE = os.path.join(self.cache, "thumbnails")
        main.METADATA_CACHE = os.path.join(self.cache, "metadata")
        main.CHARACTER_CACHE = os.path.join(self.cache, "characters")
        main.EPISODE_CACHE = os.path.join(self.cache, "episodes")
        main.SUBTITLE_CACHE = os.path.join(self.cache, "subtitles")
        main.SEIYUU_CACHE = os.path.join(self.cache, "seiyuu")
        main.WATCH_HISTORY_FILE = os.path.join(self.cache, "watch_history.json")
        main.WATCH_STATUS_FILE = os.path.join(self.cache, "watch_status.json")
        main.get_cached_anilist_info = lambda _name: {}

        if main.LIBRARY_SYNC_LOCK.locked():
            self.fail("Library sync lock was unexpectedly locked before test")

        main.init_db()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(main, name, value)

    def make_anime(self, library_path, name, episode="Episode 01.mkv"):
        anime_path = os.path.join(library_path, name)
        os.makedirs(anime_path, exist_ok=True)
        open(os.path.join(anime_path, episode), "wb").close()
        return anime_path

    def db_names(self):
        with main.db_connection() as conn:
            return {
                row[0]
                for row in conn.execute("SELECT name FROM anime_library")
            }

    def write_watch_data(self):
        main.save_watch_history({
            "Keep Me": {
                "episode": "Episode 01.mkv",
                "media_name": "Keep Me",
            }
        })
        main.save_watch_status({
            "Keep Me": {
                "Episode 01.mkv": {
                    "watched": True,
                    "progress": 100,
                }
            }
        })

    def test_normal_library_sync_adds_anime(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Demo Anime")

        result = main.sync_all_library("test normal sync")

        self.assertFalse(result["skipped"])
        self.assertIn("Demo Anime", self.db_names())
        self.assertFalse(main.LIBRARY_SYNC_LOCK.locked())

    def test_deleted_anime_removes_active_row_but_preserves_watch_data(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        self.make_anime(self.library, "Still Here")
        self.write_watch_data()
        main.sync_all_library("test initial sync")

        shutil.rmtree(os.path.join(self.library, "Keep Me"))
        result = main.sync_all_library("test deleted anime sync")

        self.assertEqual(result["stale_count"], 1)
        self.assertNotIn("Keep Me", self.db_names())
        self.assertIn("Still Here", self.db_names())
        self.assertIn("Keep Me", main.load_history_data())
        self.assertIn("Keep Me", main.load_watch_status())

    def test_suddenly_empty_root_skips_stale_cleanup_and_preserves_watch_data(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        self.write_watch_data()
        main.sync_all_library("test initial sync")

        shutil.rmtree(os.path.join(self.library, "Keep Me"))
        result = main.sync_all_library("test suspicious empty sync")

        self.assertTrue(result["stale_cleanup_skipped"])
        self.assertIn("Keep Me", self.db_names())
        self.assertIn("Keep Me", main.load_history_data())
        self.assertIn("Keep Me", main.load_watch_status())

    def test_missing_root_skips_destructive_cleanup(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        self.write_watch_data()
        main.sync_all_library("test initial sync")

        shutil.rmtree(self.library)
        result = main.sync_all_library("test missing root sync")

        self.assertEqual(result["reason"], "no_valid_library_paths")
        self.assertIn("Keep Me", self.db_names())
        self.assertIn("Keep Me", main.load_history_data())
        self.assertIn("Keep Me", main.load_watch_status())

    def test_scan_exception_skips_stale_cleanup_but_adds_found_anime(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        self.write_watch_data()
        main.sync_all_library("test initial sync")

        self.make_anime(self.library, "New Anime")
        original_os_walk = main.os.walk

        def failing_walk(path, *args, **kwargs):
            if os.path.basename(path) == "Keep Me":
                onerror = kwargs.get("onerror")
                if onerror:
                    onerror(OSError("simulated scan failure"))
                return
            yield from original_os_walk(path, *args, **kwargs)

        main.os.walk = failing_walk
        try:
            result = main.sync_all_library("test scan exception sync")
        finally:
            main.os.walk = original_os_walk

        self.assertTrue(result["stale_cleanup_skipped"])
        self.assertIn("Keep Me", self.db_names())
        self.assertIn("New Anime", self.db_names())
        self.assertFalse(main.LIBRARY_SYNC_LOCK.locked())

    def test_one_root_success_and_one_root_failure_skips_stale_cleanup(self):
        os.makedirs(self.library)
        os.makedirs(self.second_library)
        main.ANIME_PATHS = [self.library, self.second_library]
        self.make_anime(self.library, "Keep Me")
        self.make_anime(self.second_library, "Other Root Anime")
        main.sync_all_library("test initial multi root sync")

        shutil.rmtree(self.second_library)
        self.make_anime(self.library, "New Anime")
        result = main.sync_all_library("test partial root failure sync")

        self.assertTrue(result["stale_cleanup_skipped"])
        self.assertIn("Other Root Anime", self.db_names())
        self.assertIn("New Anime", self.db_names())

    def test_drive_available_again_after_failure_updates_library(self):
        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        main.sync_all_library("test initial sync")

        shutil.rmtree(self.library)
        missing_result = main.sync_all_library("test missing root sync")
        self.assertEqual(missing_result["reason"], "no_valid_library_paths")

        os.makedirs(self.library)
        self.make_anime(self.library, "Keep Me")
        self.make_anime(self.library, "Returned Anime")
        result = main.sync_all_library("test returned root sync")

        self.assertFalse(result["stale_cleanup_skipped"])
        self.assertIn("Keep Me", self.db_names())
        self.assertIn("Returned Anime", self.db_names())


if __name__ == "__main__":
    unittest.main()
