# tests/test_fts5_hwaccel.py
"""Unit tests for SQLite FTS5 Full-Text Search and FFmpeg Hardware Acceleration."""

import os
import tempfile
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from anibase.db import (
    db_connection,
    init_db,
    sanitize_fts5_query,
    sync_anime_fts,
    delete_anime_fts,
    search_anime_fts,
    rebuild_anime_fts,
)
from anibase.ffmpeg import (
    detect_available_hwaccels,
    get_ffmpeg_hwaccel_args,
    get_media_dependency_diagnostics,
)
from anibase.web.helpers import get_spotlight_results


class TestFTS5Search(unittest.TestCase):
    """Test suite for SQLite FTS5 database virtual table and search queries."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_anibase.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sanitize_fts5_query(self):
        """Verify query sanitizer handles various inputs safely."""
        self.assertIsNone(sanitize_fts5_query(None))
        self.assertIsNone(sanitize_fts5_query(""))
        self.assertIsNone(sanitize_fts5_query("   "))
        self.assertIsNone(sanitize_fts5_query("!@#$%^&*()_+{}[]:;\"'<>?,./"))

        # Single word
        self.assertEqual(sanitize_fts5_query("naruto"), '"naruto"*')

        # Multiple words
        result = sanitize_fts5_query("steins gate zero")
        self.assertEqual(result, '"steins"* "gate"* "zero"*')

        # With special characters and diacritics
        result_special = sanitize_fts5_query("frieren: beyond journey's end!")
        self.assertIn('"frieren"*', result_special)
        self.assertIn('"beyond"*', result_special)
        self.assertIn('"journey"*', result_special)
        self.assertIn('"end"*', result_special)

    def test_fts5_crud_and_bm25_ranking(self):
        """Verify adding, searching with BM25, and deleting from anime_fts."""
        with db_connection(self.db_path) as conn:
            # Sync entries
            sync_anime_fts(
                conn,
                name="Steins;Gate",
                title="Steins;Gate",
                genres=["Sci-Fi", "Thriller"],
                synonyms=["Steins Gate", "SG"],
                description="A group of friends invent a time-traveling microwave gadget.",
            )
            sync_anime_fts(
                conn,
                name="Attack on Titan",
                title="Attack on Titan",
                genres=["Action", "Fantasy", "Drama"],
                synonyms=["Shingeki no Kyojin"],
                description="Humanity lives inside enormous walls to hide from man-eating titans.",
            )
            sync_anime_fts(
                conn,
                name="Sousou no Frieren",
                title="Frieren: Beyond Journey's End",
                genres=["Adventure", "Drama", "Fantasy"],
                synonyms=["Sousou no Frieren", "Frieren the Slayer"],
                description="An elf mage embarks on a nostalgic journey across the lands.",
            )

            # 1. Search title match
            results = search_anime_fts("Steins", limit=10, conn=conn)
            self.assertTrue(len(results) >= 1)
            self.assertEqual(results[0]["name"], "Steins;Gate")
            self.assertIsInstance(results[0]["rank"], float)

            # 2. Search synopsis / description match (word "microwave" is ONLY in description)
            results_synopsis = search_anime_fts("microwave", limit=10, conn=conn)
            self.assertEqual(len(results_synopsis), 1)
            self.assertEqual(results_synopsis[0]["name"], "Steins;Gate")

            # 3. Search synonym match ("Shingeki")
            results_synonym = search_anime_fts("Shingeki", limit=10, conn=conn)
            self.assertEqual(len(results_synonym), 1)
            self.assertEqual(results_synonym[0]["name"], "Attack on Titan")

            # 4. Search description keyword ("elf mage")
            results_elf = search_anime_fts("elf mage", limit=10, conn=conn)
            self.assertEqual(len(results_elf), 1)
            self.assertEqual(results_elf[0]["name"], "Sousou no Frieren")

            # 5. Delete entry and verify it's removed from search
            delete_anime_fts(conn, "Steins;Gate")
            results_after_delete = search_anime_fts("Steins", limit=10, conn=conn)
            self.assertEqual(len(results_after_delete), 0)

    def test_rebuild_anime_fts(self):
        """Verify rebuilding FTS index repopulates all library entries."""
        with db_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO anime_library (name, episodes, score, genres, year, season, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Bocchi the Rock!", 12, 8.8, '["Comedy", "Music"]', 2022, "Fall", "FINISHED"),
            )

        with patch("anibase.metadata.get_cached_metadata_only") as mock_meta:
            mock_meta.return_value = {
                "title": "Bocchi the Rock!",
                "genres": ["Comedy", "Music"],
                "synonyms": ["BTR"],
                "description": "Hitori Gotoh is a lonely high school girl who plays the guitar.",
            }
            with db_connection(self.db_path) as conn:
                count = rebuild_anime_fts(conn)
                self.assertEqual(count, 1)
                results = search_anime_fts("guitar", limit=5, conn=conn)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["name"], "Bocchi the Rock!")


class TestFFmpegHWAccel(unittest.TestCase):
    """Test suite for FFmpeg Hardware Acceleration detection and parameters."""

    def test_detect_available_hwaccels(self):
        """Verify detect_available_hwaccels returns dictionary with methods and status."""
        hw_info = detect_available_hwaccels()
        self.assertIsInstance(hw_info, dict)
        self.assertIn("methods", hw_info)
        self.assertIn("selected", hw_info)
        self.assertIn("available", hw_info)
        self.assertIsInstance(hw_info["methods"], list)
        # Result should be cached on subsequent calls
        hw_info_second = detect_available_hwaccels()
        self.assertEqual(hw_info, hw_info_second)

    def test_get_ffmpeg_hwaccel_args(self):
        """Verify get_ffmpeg_hwaccel_args returns valid ffmpeg CLI arguments."""
        args = get_ffmpeg_hwaccel_args()
        self.assertIsInstance(args, list)
        if args:
            self.assertEqual(args[0], "-hwaccel")

    def test_media_dependency_diagnostics_includes_hwaccel(self):
        """Verify get_media_dependency_diagnostics includes detailed hwaccel status."""
        diag = get_media_dependency_diagnostics()
        self.assertIn("hwaccel", diag)
        hwaccel_info = diag["hwaccel"]
        self.assertIn("available", hwaccel_info)
        self.assertIn("path", hwaccel_info)
        self.assertIn("status", hwaccel_info)
        self.assertIn("message", hwaccel_info)
        self.assertIsInstance(hwaccel_info["available"], bool)

    def test_hwaccel_selects_vaapi_on_linux(self):
        """On Linux with vaapi available, vaapi should be selected over d3d11va."""
        import anibase.ffmpeg as ffmpeg_mod
        with patch("anibase.ffmpeg.os") as mock_os, \
             patch.object(ffmpeg_mod, "_HWACCEL_CACHE", None), \
             patch.object(ffmpeg_mod, "get_media_tool_path", return_value="/usr/bin/ffmpeg"), \
             patch("anibase.ffmpeg.run_hidden_subprocess") as mock_subp:
            mock_os.name = "posix"
            mock_os.path.isfile.return_value = True
            mock_os.environ.get.side_effect = lambda k, d="": ""
            mock_subp.return_value.returncode = 0
            mock_subp.return_value.stdout = "Hardware acceleration methods:\nvaapi\ncuda\nvulkan\n"
            hw = ffmpeg_mod.detect_available_hwaccels(force=True)
            self.assertIn("vaapi", hw["methods"])
            self.assertEqual(hw["selected"], "vaapi")

    def test_hwaccel_selects_d3d11va_on_windows(self):
        """On Windows with d3d11va available, d3d11va should be selected."""
        import anibase.ffmpeg as ffmpeg_mod
        with patch("anibase.ffmpeg.os") as mock_os, \
             patch.object(ffmpeg_mod, "_HWACCEL_CACHE", None), \
             patch.object(ffmpeg_mod, "get_media_tool_path", return_value="ffmpeg.exe"), \
             patch("anibase.ffmpeg.run_hidden_subprocess") as mock_subp:
            mock_os.name = "nt"
            mock_os.path.isfile.return_value = True
            mock_os.environ.get.side_effect = lambda k, d="": ""
            mock_subp.return_value.returncode = 0
            mock_subp.return_value.stdout = "Hardware acceleration methods:\nd3d11va\ndxva2\ncuda\n"
            hw = ffmpeg_mod.detect_available_hwaccels(force=True)
            self.assertIn("d3d11va", hw["methods"])
            self.assertEqual(hw["selected"], "d3d11va")

    def test_hwaccel_env_override(self):
        """ANIBASE_HWACCEL env var overrides automatic selection."""
        import anibase.ffmpeg as ffmpeg_mod
        with patch("anibase.ffmpeg.os") as mock_os, \
             patch.object(ffmpeg_mod, "_HWACCEL_CACHE", None), \
             patch.object(ffmpeg_mod, "get_media_tool_path", return_value="/usr/bin/ffmpeg"), \
             patch("anibase.ffmpeg.run_hidden_subprocess") as mock_subp:
            mock_os.name = "posix"
            mock_os.path.isfile.return_value = True
            mock_os.environ.get.side_effect = lambda k, d="": "cuda" if k == "ANIBASE_HWACCEL" else ""
            mock_subp.return_value.returncode = 0
            mock_subp.return_value.stdout = "Hardware acceleration methods:\nvaapi\ncuda\n"
            hw = ffmpeg_mod.detect_available_hwaccels(force=True)
            self.assertEqual(hw["selected"], "cuda")


class TestSpotlightFtsIntegration(unittest.TestCase):
    """Test spotlight search integration with FTS5."""

    def test_spotlight_results_with_fts_synopsis(self):
        """Verify spotlight search identifies anime matching keywords via FTS."""
        with patch("anibase.web.helpers.get_anime") as mock_get_anime, \
             patch("anibase.web.helpers.search_anime_fts") as mock_fts, \
             patch("anibase.web.helpers.get_cached_metadata_only") as mock_meta, \
             patch("anibase.web.helpers.get_movies") as mock_movies:

            mock_get_anime.return_value = [
                {
                    "name": "Bocchi the Rock!",
                    "score": 8.8,
                    "episodes": 12,
                    "year": 2022,
                    "watch_status_kind": "watching",
                    "watch_status_label": "Watching",
                    "watch_progress_label": "Ep 5 / 12",
                }
            ]
            mock_movies.return_value = []
            mock_meta.return_value = {
                "title": "Bocchi the Rock!",
                "genres": ["Comedy", "Music"],
                "synonyms": ["BTR"],
                "description": "An anxious girl joins a rock band.",
            }
            # Simulate FTS finding a match for "anxious" (which is only in description)
            mock_fts.return_value = [{"name": "Bocchi the Rock!", "rank": -3.5}]

            results = get_spotlight_results(query="anxious")
            self.assertTrue(results["ok"])
            anime_matches = results["results"]["anime"]
            self.assertEqual(len(anime_matches), 1)
            self.assertEqual(anime_matches[0]["name"], "Bocchi the Rock!")
            self.assertGreater(anime_matches[0]["relevance"], 20)

    def test_sync_all_library_no_enrich_skips_network_metadata_fetch(self):
        import main
        from anibase.scanner import sync_all_library

        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "Anime")
            show_dir = os.path.join(lib_dir, "Offline Show")
            cache_dir = os.path.join(tmpdir, "cache")
            meta_dir = os.path.join(cache_dir, "metadata")
            db_path = os.path.join(cache_dir, "library.db")
            os.makedirs(show_dir, exist_ok=True)
            os.makedirs(meta_dir, exist_ok=True)
            with open(os.path.join(show_dir, "Episode 01.mkv"), "wb") as f:
                f.write(b"video")

            orig_paths = main.ANIME_PATHS
            orig_cache = main.CACHE_DIR
            orig_meta = main.METADATA_CACHE
            orig_db = main.DB_PATH
            try:
                main.ANIME_PATHS = [lib_dir]
                main.CACHE_DIR = cache_dir
                main.METADATA_CACHE = meta_dir
                main.DB_PATH = db_path
                main.init_db(db_path)

                with patch("anibase.scanner.get_cached_anilist_info") as mock_fetch:
                    result = sync_all_library("Fast test sync", enrich_metadata=False)
                    self.assertTrue(result["ok"])
                    mock_fetch.assert_not_called()
            finally:
                main.ANIME_PATHS = orig_paths
                main.CACHE_DIR = orig_cache
                main.METADATA_CACHE = orig_meta
                main.DB_PATH = orig_db


class TestMediaPlayerDetection(unittest.TestCase):
    """Test suite for cross-platform media player path detection."""

    def test_detect_installed_media_players_returns_list(self):
        """detect_installed_media_players() always returns a list (may be empty)."""
        from anibase.player import detect_installed_media_players
        result = detect_installed_media_players()
        self.assertIsInstance(result, list)
        for item in result:
            self.assertIn("name", item)
            self.assertIn("path", item)
            self.assertIn("exists", item)
            self.assertTrue(item["exists"])

    def test_detect_installed_media_players_finds_mocked_vlc(self):
        """detect_installed_media_players() finds VLC when it exists at a known path."""
        from anibase.player import detect_installed_media_players
        with patch("anibase.player.os.path.isfile") as mock_isfile, \
             patch("anibase.player.shutil.which", return_value=None):
            # Only return True for /usr/bin/vlc
            mock_isfile.side_effect = lambda p: p == "/usr/bin/vlc"
            result = detect_installed_media_players()
            names = [r["name"] for r in result]
            self.assertIn("VLC Media Player", names)

    def test_detect_installed_media_players_finds_mocked_mpv(self):
        """detect_installed_media_players() finds MPV on Linux via shutil.which."""
        from anibase.player import detect_installed_media_players
        with patch("anibase.player.os.name", "posix"), \
             patch("anibase.player.os.path.isfile") as mock_isfile, \
             patch("anibase.player.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/mpv" if cmd == "mpv" else None
            # which result is prepended, then isfile checks it
            mock_isfile.side_effect = lambda p: p == "/usr/bin/mpv"
            result = detect_installed_media_players()
            names = [r["name"] for r in result]
            self.assertIn("MPV Player", names)

    def test_pick_native_path_linux_uses_zenity(self):
        """pick_native_path on Linux calls zenity when available."""
        from anibase.web.helpers import _pick_path_linux
        with patch("anibase.web.helpers.shutil.which", return_value="/usr/bin/zenity"), \
             patch("anibase.web.helpers.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "/home/user/Videos\n"
            result = _pick_path_linux("folder")
            self.assertEqual(result, "/home/user/Videos")
            args = mock_run.call_args[0][0]
            self.assertIn("zenity", args)
            self.assertIn("--directory", args)

    def test_pick_native_path_linux_falls_back_to_kdialog(self):
        """pick_native_path on Linux falls back to kdialog when zenity is unavailable."""
        from anibase.web.helpers import _pick_path_linux
        with patch("anibase.web.helpers.shutil.which", side_effect=lambda cmd: None if cmd == "zenity" else "/usr/bin/kdialog"), \
             patch("anibase.web.helpers.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "/home/user/Downloads\n"
            result = _pick_path_linux("folder")
            self.assertEqual(result, "/home/user/Downloads")
            args = mock_run.call_args[0][0]
            self.assertIn("kdialog", args)

    def test_pick_native_path_linux_returns_empty_when_cancelled(self):
        """_pick_path_linux() returns '' when user cancels (returncode != 0)."""
        from anibase.web.helpers import _pick_path_linux
        with patch("anibase.web.helpers.shutil.which", return_value="/usr/bin/zenity"), \
             patch("anibase.web.helpers.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            result = _pick_path_linux("folder")
            self.assertEqual(result, "")

    def test_pick_native_path_linux_falls_back_to_tkinter(self):
        """_pick_path_linux() falls back to tkinter when zenity and kdialog are missing."""
        from anibase.web.helpers import _pick_path_linux
        mock_tk = MagicMock()
        mock_filedialog = MagicMock()
        mock_filedialog.askdirectory.return_value = "/home/user/Anime"
        mock_tk.filedialog = mock_filedialog
        with patch("anibase.web.helpers.shutil.which", return_value=None), \
             patch.dict("os.environ", {"DISPLAY": ":0"}), \
             patch.dict("sys.modules", {"tkinter": mock_tk, "tkinter.filedialog": mock_filedialog}):
            result = _pick_path_linux("folder")
            self.assertEqual(result, "/home/user/Anime")

    def test_pick_windows_path_is_backward_compatible_alias(self):
        """pick_windows_path calls pick_native_path for backward compatibility."""
        from anibase.web.helpers import pick_windows_path
        with patch("anibase.web.helpers.pick_native_path", return_value="mock_result") as mock_pick:
            res = pick_windows_path("folder")
            mock_pick.assert_called_once_with("folder")
            self.assertEqual(res, "mock_result")


if __name__ == "__main__":
    unittest.main()
