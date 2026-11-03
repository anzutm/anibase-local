# tests/test_fts5_hwaccel.py
"""Unit tests for SQLite FTS5 Full-Text Search and FFmpeg Hardware Acceleration."""

import os
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
