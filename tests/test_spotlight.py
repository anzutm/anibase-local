import unittest
from anibase.web import create_app


class SpotlightSearchApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_spotlight_empty_query_returns_library_and_actions(self):
        response = self.client.get("/api/search/spotlight")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("results", data)
        self.assertIn("anime", data["results"])
        self.assertIn("movies", data["results"])
        self.assertIn("actions", data["results"])
        self.assertGreaterEqual(len(data["results"]["actions"]), 4)

    def test_spotlight_action_query_filtering(self):
        response = self.client.get("/api/search/spotlight?q=settings")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        actions = data["results"]["actions"]
        self.assertTrue(any("Settings" in a["title"] for a in actions))

    def test_spotlight_limit_parameter_is_respected(self):
        response = self.client.get("/api/search/spotlight?limit=2")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertLessEqual(len(data["results"]["anime"]), 2)

    def test_spotlight_nonexistent_query(self):
        response = self.client.get("/api/search/spotlight?q=zzxyqwe_not_found_123")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["results"]["anime"]), 0)
        self.assertEqual(len(data["results"]["movies"]), 0)

    def test_spotlight_templates_and_assets_integrated(self):
        import os
        from build import REQUIRED_ASSETS
        project_root = os.path.dirname(os.path.dirname(__file__))

        self.assertTrue(os.path.exists(os.path.join(project_root, "templates", "_spotlight.html")))
        self.assertTrue(os.path.exists(os.path.join(project_root, "static", "spotlight.css")))
        self.assertTrue(os.path.exists(os.path.join(project_root, "static", "spotlight.js")))

        self.assertIn("templates/_spotlight.html", REQUIRED_ASSETS)
        self.assertIn("static/spotlight.css", REQUIRED_ASSETS)
        self.assertIn("static/spotlight.js", REQUIRED_ASSETS)

        # Check base.html renders spotlight
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn('id="spotlightOverlay"', html)
        self.assertIn('spotlight.css', html)
        self.assertIn('spotlight.js', html)


if __name__ == "__main__":
    unittest.main()

