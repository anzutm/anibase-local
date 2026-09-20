import os
import threading
import time
import unittest

from flask import Flask, Response, send_from_directory
from werkzeug.serving import make_server


class EpisodeThumbnailBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ANIBASE_BROWSER_TESTS") == "1",
                         "Set ANIBASE_BROWSER_TESTS=1 to run browser integration tests")
    def test_queue_limits_requests_and_recovers_without_reload(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        project_root = os.path.dirname(os.path.dirname(__file__))
        app = Flask(__name__)
        attempts = {}
        activity = {"current": 0, "maximum": 0}
        lock = threading.Lock()

        @app.get("/")
        def index():
            cards = "".join(
                '<img class="episode-thumbnail" '
                'src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" '
                f'data-thumbnail-src="/thumbnail/{index}">' for index in range(5)
            )
            return (
                '<!doctype html><html><body>'
                f'{cards}<script src="/assets/episode-thumbnails.js"></script>'
                '</body></html>'
            )

        @app.get("/assets/<path:name>")
        def assets(name):
            return send_from_directory(os.path.join(project_root, "static"), name)

        @app.get("/thumbnail/<int:index>")
        def thumbnail(index):
            with lock:
                attempts[index] = attempts.get(index, 0) + 1
                activity["current"] += 1
                activity["maximum"] = max(activity["maximum"], activity["current"])
            try:
                time.sleep(0.08)
                if index == 0 and attempts[index] == 1:
                    return Response("", status=503, headers={"Retry-After": "1"})
                return Response(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">'
                    '<rect width="2" height="2" fill="black"/></svg>',
                    mimetype="image/svg+xml"
                )
            finally:
                with lock:
                    activity["current"] -= 1

        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 900, "height": 700})
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.wait_for_function(
                    "[...document.querySelectorAll('[data-thumbnail-src]')]"
                    ".every(image => image.dataset.thumbnailState === 'ready')",
                    timeout=10000
                )
                self.assertEqual(page.evaluate("performance.getEntriesByType('navigation').length"), 1)
                self.assertEqual(page.locator('[data-thumbnail-state="ready"]').count(), 5)
                self.assertGreaterEqual(attempts.get(0, 0), 2)
                self.assertLessEqual(activity["maximum"], 2)
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
