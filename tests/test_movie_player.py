import os
import tempfile
import unittest
from unittest.mock import patch

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

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
