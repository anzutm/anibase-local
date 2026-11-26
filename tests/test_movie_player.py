import os
import tempfile
import unittest
from unittest.mock import patch
import requests

from anibase.web import create_app
import anibase.constants as constants


class MoviePlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.movie_dir = os.path.join(self.temp_dir.name, "Movies")
        os.makedirs(self.movie_dir, exist_ok=True)

        self.movie1_file = "Suzume.mkv"
        self.movie2_file = "Your Name.mkv"

        with open(os.path.join(self.movie_dir, self.movie1_file), "wb") as f:
            f.write(b"video-data-1")
        with open(os.path.join(self.movie_dir, self.movie2_file), "wb") as f:
            f.write(b"video-data-2")

        self.orig_movie_path = constants.MOVIE_PATH
        constants.MOVIE_PATH = self.movie_dir

        self.req_patcher = patch("requests.get", side_effect=requests.RequestException("No network in tests"))
        self.req_patcher.start()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.req_patcher.stop()
        constants.MOVIE_PATH = self.orig_movie_path
        self.temp_dir.cleanup()

    def test_movie_detail_links_to_cinema_player(self):
        response = self.client.get(f"/movie/{self.movie1_file}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # Should link directly to /player/Movies/<file>
        self.assertIn(f"/player/Movies/{self.movie1_file}", html)
        self.assertIn("movie-cinema-launch", html)
        self.assertIn("Open Cinema Player", html)

    def test_movies_page_links_to_cinema_player(self):
        from urllib.parse import quote
        response = self.client.get("/movies")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f"/player/Movies/{quote(self.movie1_file)}", html)
        self.assertIn(f"/player/Movies/{quote(self.movie2_file)}", html)

    def test_player_for_movies_route_and_metadata(self):
        response = self.client.get(f"/player/Movies/{self.movie1_file}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Check clean movie title in title and data attributes
        self.assertIn("Suzume", html)
        self.assertIn('data-is-movie="1"', html)
        self.assertIn('data-display-title="Suzume"', html)

        # Check collection queue presentation
        self.assertIn("Movie Collection", html)
        self.assertIn("More Movies", html)

        # Other movie in collection should be in queue
        self.assertIn("Your Name", html)

    def test_player_movie_navigation_links(self):
        response = self.client.get(f"/player/Movies/{self.movie1_file}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # Back button links back to movie detail
        self.assertIn(f"/movie/{self.movie1_file}", html)

    def test_player_pip_and_mini_floating_elements(self):
        response = self.client.get(f"/player/Movies/{self.movie1_file}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('id="playerPipBtn"', html)
        self.assertNotIn('id="playerEpisodesDrawerBtn"', html)
        self.assertIn('id="playerMiniFloatBtn"', html)
        self.assertIn('id="playerPlaceholder"', html)
        self.assertIn('id="miniPlayerOverlay"', html)
        self.assertIn('id="miniPlayerExpandBtn"', html)
        self.assertIn('id="miniPlayerCloseBtn"', html)
        self.assertIn('id="miniPlayerBrowseBtn"', html)

    def test_global_persistent_mini_player_in_base_pages(self):
        from build import REQUIRED_ASSETS
        self.assertIn("templates/_global_mini_player.html", REQUIRED_ASSETS)
        self.assertIn("static/global-mini-player.css", REQUIRED_ASSETS)
        self.assertIn("static/global-mini-player.js", REQUIRED_ASSETS)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="globalMiniPlayer"', html)
        self.assertIn('id="globalMiniVideo"', html)
        self.assertIn('global-mini-player.css', html)
        self.assertIn('global-mini-player.js', html)

        # Ensure anime details page (anime.html) also includes the global mini player
        response_detail = self.client.get(f"/movie/{self.movie1_file}")
        self.assertEqual(response_detail.status_code, 200)
        html_detail = response_detail.get_data(as_text=True)
        self.assertIn('id="globalMiniPlayer"', html_detail)
        self.assertIn('id="globalMiniVideo"', html_detail)
        self.assertIn('global-mini-player.css', html_detail)
        self.assertIn('global-mini-player.js', html_detail)

    def test_delete_movie_cleans_v2_thumbnail_and_seek_preview_cache(self):
        import main
        from anibase.settings import get_action_token
        from anibase.thumbnails import get_thumbnail_cache_path, seek_preview_identity

        cache_root = os.path.join(self.temp_dir.name, "cache")
        thumb_root = os.path.join(cache_root, "thumbnails")
        os.makedirs(thumb_root, exist_ok=True)

        orig = {
            "CACHE_DIR": main.CACHE_DIR,
            "THUMBNAIL_CACHE": main.THUMBNAIL_CACHE,
            "SETTINGS_FILE": main.SETTINGS_FILE,
            "DB_PATH": main.DB_PATH,
            "WATCH_HISTORY_FILE": main.WATCH_HISTORY_FILE,
            "WATCH_STATUS_FILE": main.WATCH_STATUS_FILE,
            "METADATA_CACHE": main.METADATA_CACHE,
            "POSTER_CACHE": main.POSTER_CACHE,
            "BANNER_CACHE": main.BANNER_CACHE,
            "EPISODE_CACHE": main.EPISODE_CACHE,
            "SUBTITLE_CACHE": main.SUBTITLE_CACHE,
            "CHARACTER_CACHE": main.CHARACTER_CACHE,
        }
        try:
            main.CACHE_DIR = cache_root
            main.THUMBNAIL_CACHE = thumb_root
            main.SETTINGS_FILE = os.path.join(self.temp_dir.name, "settings.json")
            main.DB_PATH = os.path.join(cache_root, "library.db")
            main.WATCH_HISTORY_FILE = os.path.join(cache_root, "watch_history.json")
            main.WATCH_STATUS_FILE = os.path.join(cache_root, "watch_status.json")
            main.METADATA_CACHE = os.path.join(cache_root, "metadata")
            main.POSTER_CACHE = os.path.join(cache_root, "posters")
            main.BANNER_CACHE = os.path.join(cache_root, "banners")
            main.EPISODE_CACHE = os.path.join(cache_root, "episodes")
            main.SUBTITLE_CACHE = os.path.join(cache_root, "subtitles")
            main.CHARACTER_CACHE = os.path.join(cache_root, "characters")
            main.init_db(main.DB_PATH)
            main.save_settings({
                "setup_completed": True,
                "library_paths": [self.temp_dir.name],
                "movie_path": self.movie_dir,
            })

            movie_full_path = os.path.join(self.movie_dir, self.movie1_file)
            thumb_v2_path = get_thumbnail_cache_path(movie_full_path)
            seek_folder, _ = seek_preview_identity(movie_full_path)
            os.makedirs(seek_folder, exist_ok=True)
            with open(thumb_v2_path, "wb") as f:
                f.write(b"thumb")
            with open(os.path.join(seek_folder, "sprite-01.jpg"), "wb") as f:
                f.write(b"sprite")

            token = get_action_token()
            resp = self.client.post(
                "/api/anime/delete",
                headers={"X-Action-Token": token},
                json={"anime_name": "Movies", "is_movie": True, "filename": self.movie1_file},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(os.path.exists(movie_full_path))
            self.assertFalse(os.path.exists(thumb_v2_path))
            self.assertFalse(os.path.exists(seek_folder))
        finally:
            for key, value in orig.items():
                setattr(main, key, value)


if __name__ == "__main__":
    unittest.main()
