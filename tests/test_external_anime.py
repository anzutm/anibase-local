import unittest
from unittest.mock import patch
import main

class ExternalAnimeTests(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()
        self.sample_anime = {
            "id": 16498,
            "id_mal": 16498,
            "name": "Attack on Titan",
            "title_english": "Attack on Titan",
            "title_romaji": "Shingeki no Kyojin",
            "title_native": "進撃の巨人",
            "synonyms": ["SnK", "AoT"],
            "description": "Centuries ago, mankind was slaughtered to near extinction by monstrous humanoid creatures called Titans.",
            "format": "TV",
            "status": "Finished",
            "season": "Spring",
            "year": 2013,
            "episodes": 25,
            "duration": 24,
            "start_date": "2013-04-07",
            "end_date": "2013-09-29",
            "score": 85,
            "mean_score": 84,
            "popularity": 500000,
            "favourites": 120000,
            "genres": ["Action", "Fantasy", "Drama"],
            "tags": ["Military", "Post-Apocalyptic", "Gore"],
            "poster": "https://example.com/poster.jpg",
            "banner": "https://example.com/banner.jpg",
            "cover_color": "#3b82f6",
            "studio": "Wit Studio",
            "trailer": {
                "site": "youtube",
                "id": "sample_trailer_id",
                "embed_url": "https://www.youtube-nocookie.com/embed/sample_trailer_id",
                "watch_url": "https://www.youtube.com/watch?v=sample_trailer_id",
                "thumbnail": "https://example.com/thumb.jpg"
            },
            "next_airing": None,
            "characters": [
                {
                    "character_id": 40882,
                    "character_name": "Eren Jaeger",
                    "character_native": "エレン・イェーガー",
                    "character_image": "https://example.com/eren.jpg",
                    "role": "Main",
                    "va_id": 80,
                    "va_name": "Yuki Kaji",
                    "va_native": "梶 裕貴",
                    "va_image": "https://example.com/kaji.jpg"
                }
            ],
            "relations": [
                {
                    "id": 20958,
                    "relation_type": "Sequel",
                    "format": "TV",
                    "status": "Finished",
                    "title": "Attack on Titan Season 2",
                    "title_romaji": "Shingeki no Kyojin Season 2",
                    "poster": "https://example.com/s2.jpg"
                }
            ],
            "recommendations": [
                {
                    "id": 21459,
                    "format": "TV",
                    "status": "Finished",
                    "score": 82,
                    "title": "Kabaneri of the Iron Fortress",
                    "title_romaji": "Koutetsujou no Kabaneri",
                    "poster": "https://example.com/kabaneri.jpg"
                }
            ],
            "anilist_url": "https://anilist.co/anime/16498",
            "mal_url": "https://myanimelist.net/anime/16498",
            "provider": "AniList"
        }

    def test_format_time_until_airing(self):
        self.assertIsNone(main.format_time_until_airing(None))
        self.assertIsNone(main.format_time_until_airing(0))
        self.assertIsNone(main.format_time_until_airing(-10))
        self.assertEqual(main.format_time_until_airing(86400 * 3 + 3600 * 5), "3d 5h")
        self.assertEqual(main.format_time_until_airing(3600 * 2 + 60 * 30), "2h 30m")
        self.assertEqual(main.format_time_until_airing(60 * 45), "45m")
        self.assertEqual(main.format_time_until_airing(30), "< 1m")

    @patch("main.get_cached_external_anime_info")
    @patch("main.get_anime")
    def test_external_anime_detail_renders_successfully(self, mock_get_anime, mock_get_cached):
        mock_get_cached.return_value = self.sample_anime
        mock_get_anime.return_value = []

        resp = self.client.get("/anime/external/16498?return_to=/studio/Wit%20Studio")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Attack on Titan", html)
        self.assertIn("Wit Studio", html)
        self.assertIn("Eren Jaeger", html)
        self.assertIn("Yuki Kaji", html)
        self.assertIn("Attack on Titan Season 2", html)
        self.assertIn("Kabaneri of the Iron Fortress", html)
        self.assertIn("Back to Wit Studio", html)

    @patch("main.get_cached_external_anime_info")
    @patch("main.get_anime")
    def test_external_anime_detail_detects_local_library_match(self, mock_get_anime, mock_get_cached):
        mock_get_cached.return_value = self.sample_anime
        mock_get_anime.return_value = [
            {"name": "Attack on Titan", "status": "ongoing", "score": 90, "episodes": 25}
        ]

        resp = self.client.get("/anime/external/16498")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("DITEMUKAN DI LIBRARY LOKAL", html)
        self.assertIn("Buka & Tonton di Library", html)

    @patch("main.get_cached_external_anime_info")
    def test_external_anime_detail_not_found_returns_404(self, mock_get_cached):
        mock_get_cached.return_value = None

        resp = self.client.get("/anime/external/999999")
        self.assertEqual(resp.status_code, 404)
        html = resp.data.decode("utf-8")
        self.assertIn("Informasi Anime Tidak Ditemukan", html)
        self.assertIn("999999", html)

    @patch("main.get_cached_external_anime_info")
    def test_external_anime_detail_safe_return_to(self, mock_get_cached):
        mock_get_cached.return_value = self.sample_anime

        # External / dangerous URLs must be discarded and fall back to index
        resp = self.client.get("/anime/external/16498?return_to=http://evil.com/phishing")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn('href="/"', html)

        # Valid local URL should be accepted
        resp2 = self.client.get("/anime/external/16498?return_to=/studio/MAPPA")
        self.assertEqual(resp2.status_code, 200)
        html2 = resp2.data.decode("utf-8")
        self.assertIn('href="/studio/MAPPA"', html2)
        self.assertIn("Back to MAPPA", html2)

    @patch("main.fetch_anilist_studio_projects")
    @patch("main.get_anime")
    def test_studio_page_links_not_in_library_cards_to_external_detail(self, mock_get_anime, mock_studio_projects):
        mock_get_anime.return_value = []
        mock_studio_projects.return_value = ({
            "studio_info": {"id": 100, "name": "Test Studio"},
            "projects": [
                {
                    "id": 12345,
                    "title": {"english": "Unowned Anime", "romaji": "Unowned Anime"},
                    "coverImage": {"extraLarge": "https://example.com/poster.jpg"},
                    "format": "TV",
                    "status": "FINISHED",
                    "season": "FALL",
                    "seasonYear": 2022,
                    "episodes": 12,
                    "averageScore": 75,
                    "popularity": 1000
                }
            ],
            "provider": "AniList"
        }, None)

        resp = self.client.get("/studio/Test%20Studio")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn('/anime/external/12345', html)
        self.assertIn("Outside Library", html)
        self.assertIn("Info Anime", html)

if __name__ == "__main__":
    unittest.main()
