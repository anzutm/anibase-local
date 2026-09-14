import unittest
import tempfile
import os
import json
from unittest.mock import patch

import main


class SeasonMatchingTests(unittest.TestCase):
    def test_cached_manual_match_is_preserved(self):
        info = {"title": "User selected title", "anilist_id": 10,
                "metadata_provider": "anilist", "characters": [], "relations": [], "recommendations": []}
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "Horimiya Season 2.json"), "w") as handle:
                json.dump(info, handle)
            with patch.object(main, "METADATA_CACHE", folder), \
                    patch.object(main, "get_metadata_mapping", return_value={"anilist_id": 10, "manual": True}), \
                    patch.object(main, "get_anilist_info") as fetch:
                result = main.get_cached_anilist_info("Horimiya Season 2")
                self.assertEqual(result["anilist_id"], 10)
                fetch.assert_not_called()

    def test_old_automatic_cache_and_wrong_mapping_are_not_trusted(self):
        wrong = {"title": "Log Horizon Season 2", "anilist_id": 10,
                 "metadata_provider": "anilist", "characters": [], "relations": [], "recommendations": []}
        corrected = {**wrong, "title": "Horimiya: The Missing Pieces", "anilist_id": 20,
                     "season_match_version": 1}
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "Horimiya Season 2.json"), "w") as handle:
                json.dump(wrong, handle)
            with patch.object(main, "METADATA_CACHE", folder), \
                    patch.object(main, "get_metadata_mapping", return_value={"anilist_id": 10}), \
                    patch.object(main, "can_attempt_anilist", return_value=True), \
                    patch.object(main, "get_anilist_info", return_value=corrected) as fetch:
                result = main.get_cached_anilist_info("Horimiya Season 2")
                self.assertEqual(result["anilist_id"], 20)
                fetch.assert_called_once_with("Horimiya Season 2")

    def test_unrelated_title_cannot_win_by_season_number(self):
        wrong = {"anilist_id": 1, "title": "Log Horizon Season 2", "format": "TV"}
        original = {"anilist_id": 2, "title": "Horimiya", "format": "TV"}
        self.assertIsNone(main.select_season_candidate("Horimiya Season 2", [wrong, original]))
        self.assertEqual(main.select_season_candidate("Horimiya Season 1", [wrong, original]), original)

    def test_subtitled_second_installment_requires_relation(self):
        original = {"anilist_id": 2, "title": "Horimiya", "format": "TV", "year": 2021}
        second = {"anilist_id": 3, "title": "Horimiya: The Missing Pieces", "format": "TV", "year": 2023}
        self.assertIsNone(main.select_season_candidate("Horimiya S2", [original, second]))
        original["metadata_relations"] = [{"relationType": "SIDE_STORY", "node": {"id": 3}}]
        self.assertEqual(main.select_season_candidate("Horimiya S2", [original, second]), second)

    def test_original_and_numbered_sequel_are_not_interchangeable(self):
        first = {"anilist_id": 1, "title": "Example", "format": "TV"}
        second = {"anilist_id": 2, "title": "Example 2nd Season", "format": "TV"}
        self.assertEqual(main.select_season_candidate("Example Season 1", [second, first]), first)
        self.assertEqual(main.select_season_candidate("Example Season 2", [first, second]), second)
        self.assertIsNone(main.select_season_candidate("Example Season 3", [first, second]))

    def test_ambiguous_matches_are_rejected(self):
        candidates = [{"title": "Example Season 2", "anilist_id": 1},
                      {"title": "Example Season 2", "anilist_id": 2}]
        self.assertIsNone(main.select_season_candidate("Example S2", candidates))

    def test_season_regex_does_not_match_suffix_of_words(self):
        self.assertEqual(main.metadata_title_parts("Fruits 2"), ("fruits 2", 0))
        self.assertEqual(main.metadata_title_parts("Example S02"), ("example", 2))

    def test_wrong_anilist_candidate_does_not_trigger_detail_or_outage(self):
        with patch.object(main, "search_anilist_anime", return_value=([
                {"title": "Log Horizon Season 2", "anilist_id": 1}], None)), \
                patch.object(main.requests, "post") as request:
            self.assertIsNone(main.get_anilist_info("Horimiya Season 2"))
            self.assertFalse(main.ANILIST_CALL_STATE.last_error)
            request.assert_not_called()

    def test_tenrai_rejects_unrelated_season(self):
        with patch.object(main, "fetch_tenrai_json", return_value={"data": [
                {"mal_id": 1, "title": "Log Horizon Season 2", "type": "TV"}]}) as fetch:
            self.assertIsNone(main.get_tenrai_anime_info("Horimiya Season 2"))
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
