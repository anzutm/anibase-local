"""Unit tests for Samehadaku scraper, direct downloader, and Web API."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from anibase.integrations.samehadaku import (
    search_samehadaku,
    get_samehadaku_anime_details,
    get_samehadaku_download_links,
    get_samehadaku_latest_episodes,
    get_pixeldrain_file_info,
)
from anibase.download_manager import (
    resolve_download_folder,
    resolve_download_destination,
    find_matching_library_anime,
    start_download,
    cancel_download,
    get_download_task,
    get_all_download_tasks,
    clear_completed_download_tasks,
    sanitize_filename,
)
from anibase.web import create_app


SAMPLE_SEARCH_HTML = """
<html><body>
<div class="relat">
    <article class="animepost post-1001 anime type-anime">
        <div class="animposx">
            <a href="https://v2.samehadaku.how/anime/frieren-beyond-journeys-end/" title="Sousou no Frieren">
                <div class="content-thumb">
                    <img src="https://v2.samehadaku.how/wp-content/uploads/poster.jpg" />
                    <div class="type TV">TV</div>
                    <div class="score">9.35</div>
                </div>
                <div class="data">
                    <div class="title"><h2>Sousou no Frieren</h2></div>
                </div>
            </a>
        </div>
    </article>
    <article class="animepost post-1002 anime type-anime">
        <div class="animposx">
            <a href="https://v2.samehadaku.how/batch/sousou-no-frieren-batch/" title="Sousou no Frieren Batch">
                <div class="content-thumb">
                    <img src="https://v2.samehadaku.how/wp-content/uploads/batch.jpg" />
                    <div class="type">Batch</div>
                </div>
                <div class="data">
                    <div class="title"><h2>Sousou no Frieren Batch</h2></div>
                </div>
            </a>
        </div>
    </article>
    <!-- Episode post yang harus disaring / diabaikan dari pencarian anime -->
    <article class="animepost post-1003 episode type-episode">
        <div class="animposx">
            <a href="https://v2.samehadaku.how/sousou-no-frieren-episode-1/" title="Sousou no Frieren Episode 1">
                <div class="content-thumb">
                    <img src="https://v2.samehadaku.how/wp-content/uploads/ep1.jpg" />
                    <div class="type">Eps 1</div>
                    <div class="score">8.5</div>
                </div>
                <div class="data">
                    <div class="title"><h2>Sousou no Frieren Episode 1</h2></div>
                </div>
            </a>
        </div>
    </article>
</div>
</body></html>
"""

SAMPLE_HUB_HTML = """
<html><body>
<h1 class="entry-title">Sousou no Frieren Season 2</h1>
<meta property="og:image" content="https://v2.samehadaku.how/wp-content/uploads/og.jpg" />
<div class="entry-content-single">Sinopsis cerita perjalanan Frieren</div>
<div class="episode-list">
    <a href="https://v2.samehadaku.how/sousou-no-frieren-season-2-episode-1/">Episode 1</a>
    <a href="https://v2.samehadaku.how/sousou-no-frieren-season-2-episode-2/">Sousou no Frieren Season 2 Episode 2</a>
    <a href="https://v2.samehadaku.how/batch/sousou-no-frieren-season-2-batch/">Download Batch Anime Sousou no Frieren</a>
</div>
</body></html>
"""

SAMPLE_DOWNLOAD_HTML = """
<div class="download-eps" id="downloadb">
    <p><b>MKV</b></p>
    <ul>
        <li>
            <strong>720p</strong>
            <div class="download-eps-links">
                <span><a href="https://pixeldrain.com/u/abc12345">Pixeldrain</a></span>
                <span><a href="https://gofile.io/d/xyz987">Gofile</a></span>
            </div>
        </li>
        <li>
            <strong>1080p</strong>
            <div class="download-eps-links">
                <span><a href="https://pixeldrain.com/u/def67890">Pixeldrain</a></span>
                <span><a href="https://krakenfiles.com/view/123/file.html">Krakenfiles</a></span>
            </div>
        </li>
    </ul>
    <p><b>MP4</b></p>
    <ul>
        <li>
            <strong>480p</strong>
            <div class="download-eps-links">
                <span><a href="https://pixeldrain.com/u/mp4480">Pixeldrain</a></span>
            </div>
        </li>
    </ul>
</div>
</div>
"""


SAMPLE_LATEST_HTML = """
<html><body>
<div class="post-show">
    <ul>
        <li itemscope="itemscope" itemtype="http://schema.org/CreativeWork">
            <div class="thumb">
                <a href="https://v2.samehadaku.how/mock-anime-episode-5/" title="Mock Anime" itemprop="url">
                    <img src="https://v2.samehadaku.how/wp-content/uploads/mock-thumb.jpg" alt="Mock Anime" loading="lazy" />
                </a>
            </div>
            <div class="dtla">
                <h2 class="entry-title" itemprop="headline">
                    <a href="https://v2.samehadaku.how/anime/mock-anime/" title="Mock Anime" itemprop="url">Mock Anime</a>
                </h2>
                <span><i class="fa fa-play"></i> <b>Episode</b> 5</span>
                <span class="author vcard"><i class="fa fa-user"></i> <b>Posted by:</b> <span itemprop="name">Tester</span></span>
                <span><i class="fa fa-calendar"></i> <b>Released on:</b> 2 Jam yang lalu</span>
            </div>
        </li>
    </ul>
</div>
</body></html>
"""


class SamehadakuIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Point cache dir to temp dir so unit tests never pollute user's AppData cache
        self.patch_cache_dir = patch("anibase.integrations.samehadaku._get_cache_dir", return_value=self.temp_dir.name)
        self.patch_cache_dir.start()
        self.addCleanup(self.patch_cache_dir.stop)
        # Disable cache reading during unit tests
        self.patch_cache = patch("anibase.integrations.samehadaku._get_cached_data", return_value=None)
        self.patch_cache.start()
        self.addCleanup(self.patch_cache.stop)

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_search_samehadaku(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_SEARCH_HTML
        mock_get.return_value = mock_resp

        results = search_samehadaku("frieren")
        # Hanya serial anime dan batch yang muncul, postingan episode (Eps 1) harus disaring
        self.assertEqual(len(results), 2)
        anime_item = results[0]
        self.assertEqual(anime_item["title"], "Sousou no Frieren")
        self.assertTrue(anime_item["is_anime"])
        self.assertFalse(anime_item["is_batch"])
        self.assertFalse(anime_item["is_episode"])

        batch_item = results[1]
        self.assertTrue(batch_item["is_batch"])
        self.assertFalse(batch_item["is_episode"])

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_get_samehadaku_anime_details(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_HUB_HTML
        mock_get.return_value = mock_resp

        details = get_samehadaku_anime_details("https://v2.samehadaku.how/anime/frieren-season-2/")
        self.assertTrue(details["ok"])
        self.assertIn("Frieren", details["title"])
        self.assertFalse(details["title"].endswith("-"))
        self.assertEqual(len(details["episodes"]), 2)
        self.assertEqual(details["episodes"][0]["episode"], "1")
        self.assertIn("thumbnail", details["episodes"][0])
        self.assertTrue(bool(details["episodes"][0]["thumbnail"]))
        self.assertEqual(details["episodes"][1]["episode"], "2")
        self.assertEqual(len(details["batch_links"]), 1)

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_get_samehadaku_download_links(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DOWNLOAD_HTML
        mock_get.return_value = mock_resp

        downloads = get_samehadaku_download_links("https://v2.samehadaku.how/sousou-no-frieren-episode-1/")
        self.assertEqual(len(downloads), 3)

        # 720p MKV
        tier_720 = downloads[0]
        self.assertEqual(tier_720["format"], "MKV")
        self.assertEqual(tier_720["quality"], "720p")
        self.assertEqual(len(tier_720["links"]), 2)

        # Cek link direct Pixeldrain
        pd_link = next(l for l in tier_720["links"] if l["is_pixeldrain"])
        self.assertTrue(pd_link["is_direct"])
        self.assertEqual(pd_link["pixeldrain_id"], "abc12345")
        self.assertEqual(pd_link["direct_download_url"], "https://pixeldrain.com/api/file/abc12345?download")

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_get_pixeldrain_file_info(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "id": "abc12345",
            "name": "Frieren - 01.mkv",
            "size": 52428800,
            "mime_type": "video/x-matroska",
            "can_download": True,
        }
        mock_get.return_value = mock_resp

        info = get_pixeldrain_file_info("abc12345")
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "Frieren - 01.mkv")
        self.assertEqual(info["size"], 52428800)

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_get_samehadaku_latest_episodes(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_LATEST_HTML
        mock_get.return_value = mock_resp

        results = get_samehadaku_latest_episodes(page=1)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["title"], "Mock Anime")
        self.assertEqual(item["episode"], "Episode 5")
        self.assertEqual(item["episode_number"], "5")
        self.assertEqual(item["thumbnail"], "https://v2.samehadaku.how/wp-content/uploads/mock-thumb.jpg")
        self.assertEqual(item["released_on"], "2 Jam yang lalu")


class DownloadManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.dest_folder = self.temp_dir.name

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("Anime: Episode <01>?.mkv"), "Anime_ Episode _01__.mkv")
        self.assertTrue(sanitize_filename("").startswith("download_"))

    def test_resolve_download_folder(self):
        folder = resolve_download_folder("Test Anime Series")
        self.assertTrue(os.path.isdir(folder))

    def test_find_matching_library_anime(self):
        # Anime yang ada di folder ongoing/watchlist lokal
        match_clean = find_matching_library_anime("Test Anime Series")
        if match_clean:
            self.assertEqual(match_clean["name"], "Test Anime Series")

        # Variasi dengan strip akhiran atau episode
        match_dash = find_matching_library_anime("Test Anime Series -")
        if match_dash:
            self.assertEqual(match_dash["name"], "Test Anime Series")

        match_ep = find_matching_library_anime("Test Anime Series Episode 1")
        if match_ep:
            self.assertEqual(match_ep["name"], "Test Anime Series")

        # Anime yang tidak ada sama sekali di library
        non_existent = find_matching_library_anime("NonExistentAnime99999XYZ")
        self.assertIsNone(non_existent)

    def test_resolve_download_destination(self):
        dest = resolve_download_destination("Test Anime Series -")
        self.assertTrue(os.path.isdir(dest["folder"]))
        self.assertNotIn("-", os.path.basename(dest["folder"]))

    @patch("anibase.download_manager.requests.get")
    def test_start_and_complete_download(self, mock_get):
        # Mock streaming response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "100"}
        mock_resp.iter_content.return_value = [b"A" * 50, b"B" * 50]
        mock_get.return_value.__enter__.return_value = mock_resp

        result = start_download(
            url="https://example.com/video.mkv",
            anime_name="Mock Anime",
            episode="1",
            filename="mock_ep1.mkv",
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        task = result["task"]
        task_id = task["task_id"]

        import time
        for _ in range(30):
            status = get_download_task(task_id)
            if status and status["status"] in ("completed", "error"):
                break
            time.sleep(0.05)

        final_status = get_download_task(task_id)
        self.assertEqual(final_status["status"], "completed")
        self.assertEqual(final_status["progress_percent"], 100.0)
        self.assertTrue(os.path.isfile(final_status["save_path"]))

    def test_cancel_download(self):
        result = start_download(
            url="https://example.com/large.mkv",
            anime_name="Cancel Anime",
            filename="cancel_ep1.mkv",
            custom_dest_dir=self.dest_folder
        )
        task_id = result["task"]["task_id"]
        cancelled = cancel_download(task_id)
        self.assertTrue(cancelled)
        task = get_download_task(task_id)
        self.assertEqual(task["status"], "cancelled")


class SamehadakuApiRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    @patch("anibase.web.routes_api.get_samehadaku_latest_episodes")
    def test_api_samehadaku_latest(self, mock_latest):
        mock_latest.return_value = [{"title": "Frieren", "episode": "Episode 1", "thumbnail": "https://example.com/thumb.jpg"}]
        res = self.client.get("/api/samehadaku/latest")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["results"]), 1)

    @patch("anibase.web.routes_api.search_samehadaku")
    def test_api_samehadaku_search(self, mock_search):
        mock_search.return_value = [{"title": "Frieren", "url": "https://example.com/frieren"}]
        res = self.client.get("/api/samehadaku/search?q=frieren")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["results"]), 1)

    @patch("anibase.web.routes_api.get_samehadaku_anime_details")
    def test_api_samehadaku_episodes(self, mock_details):
        mock_details.return_value = {"ok": True, "title": "Frieren", "episodes": []}
        res = self.client.get("/api/samehadaku/episodes?url=https://example.com/anime")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

    @patch("anibase.web.routes_api.get_samehadaku_download_links")
    def test_api_samehadaku_downloads(self, mock_links):
        mock_links.return_value = [{"format": "MKV", "quality": "1080p", "links": []}]
        res = self.client.get("/api/samehadaku/downloads?url=https://example.com/ep1")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["downloads"]), 1)

    def test_api_samehadaku_download_status(self):
        res = self.client.get("/api/samehadaku/download/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["tasks"], list)

    @patch("anibase.web.routes_api.start_download")
    def test_api_samehadaku_start_download(self, mock_start):
        from anibase.settings import get_action_token
        token = get_action_token()
        mock_start.return_value = {"ok": True, "task": {"task_id": "dl_test123"}}
        res = self.client.post(
            "/api/samehadaku/download",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={"url": "https://pixeldrain.com/api/file/abc12345?download", "anime_name": "Test", "episode": "1"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["task"]["task_id"], "dl_test123")


if __name__ == "__main__":
    unittest.main()
