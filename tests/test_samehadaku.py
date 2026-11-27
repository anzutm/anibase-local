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
    pause_download,
    resume_download,
    retry_download,
    get_download_task,
    get_all_download_tasks,
    clear_completed_download_tasks,
    get_queue_overview,
    get_max_concurrent_downloads,
    set_max_concurrent_downloads,
    sanitize_filename,
    adapt_filename_to_library_pattern,
    get_anime_local_episodes,
    resolve_best_direct_link,
    start_batch_download,
    get_batch_task,
    get_all_batch_tasks,
    cancel_batch_download,
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
        self.patch_cache.stop()
        try:
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

            # Second call without force_refresh uses cache (mock_get should not be called again)
            call_count_before = mock_get.call_count
            cached_results = get_samehadaku_latest_episodes(page=1, force_refresh=False)
            self.assertEqual(mock_get.call_count, call_count_before)
            self.assertEqual(len(cached_results), 1)

            # Call with force_refresh=True must make a new network request
            refreshed_results = get_samehadaku_latest_episodes(page=1, force_refresh=True)
            self.assertEqual(mock_get.call_count, call_count_before + 1)
            self.assertEqual(len(refreshed_results), 1)
        finally:
            self.patch_cache.start()


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

    @patch("anibase.download_manager.requests.get")
    def test_download_truncated_stream_fails(self, mock_get):
        # Server klaim 500 byte tapi hanya kirim 100 byte (koneksi terputus prematur)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "500"}
        mock_resp.iter_content.return_value = [b"A" * 100]
        mock_get.return_value.__enter__.return_value = mock_resp

        result = start_download(
            url="https://example.com/truncated.mkv",
            anime_name="Truncated Anime",
            filename="truncated.mkv",
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        task_id = result["task"]["task_id"]

        import time
        for _ in range(30):
            status = get_download_task(task_id)
            if status and status["status"] in ("completed", "error"):
                break
            time.sleep(0.05)

        final_status = get_download_task(task_id)
        self.assertEqual(final_status["status"], "error")
        self.assertIn("terputus", final_status.get("error", "").lower())
        # Pastikan berkas rusak tidak dipertahankan
        self.assertFalse(os.path.isfile(final_status["save_path"]))

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

    def test_adapt_filename_to_library_pattern_with_lendrive_style(self):
        ep_files = [
            "[LENDRIVE]_Doumo_Suki_-_01_[1080p_HEVC][Dualsubs].mkv",
            "[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs].mkv",
            "[LENDRIVE]_Doumo_Suki_-_03_[1080p_HEVC][Dualsubs].mkv",
            "[LENDRIVE]_Doumo_Suki_-_04_[1080p_HEVC][Dualsubs].mkv",
            "[LENDRIVE]_Doumo_Suki_-_05_[1080p_HEVC][Dualsubs].mkv",
            "[LENDRIVE]_Doumo_Suki_-_06_[1080p_HEVC][Dualsubs].mkv",
        ]
        for name in ep_files:
            open(os.path.join(self.dest_folder, name), "wb").close()

        adapted = adapt_filename_to_library_pattern(
            folder_path=self.dest_folder,
            episode="7",
            default_filename="Doumo, Suki na Hito ni Horegusuri wo Irai sareta Majo desu. - Ep 7 [1080p].mkv"
        )
        self.assertEqual(adapted, "[LENDRIVE]_Doumo_Suki_-_07_[1080p_HEVC][Dualsubs].mkv")

    def test_adapt_filename_resolution_and_extension(self):
        ep_files = [
            "[Group]_Title_-_01_[1080p].mkv",
            "[Group]_Title_-_02_[1080p].mkv",
        ]
        for name in ep_files:
            open(os.path.join(self.dest_folder, name), "wb").close()

        adapted = adapt_filename_to_library_pattern(
            folder_path=self.dest_folder,
            episode="3",
            default_filename="Title - Ep 3 [720p].mp4"
        )
        self.assertEqual(adapted, "[Group]_Title_-_03_[720p].mp4")

    def test_adapt_filename_fallback_when_empty_or_no_match(self):
        empty_folder = os.path.join(self.dest_folder, "empty_sub")
        os.makedirs(empty_folder, exist_ok=True)
        fallback = "New Anime - Ep 1 [1080p].mkv"
        res = adapt_filename_to_library_pattern(empty_folder, "1", fallback)
        self.assertEqual(res, fallback)

    @patch("anibase.download_manager.requests.get")
    def test_start_download_adapts_filename_automatically(self, mock_get):
        for i in range(1, 4):
            open(os.path.join(self.dest_folder, f"[SubsPlease] Frieren - 0{i} (1080p).mkv"), "wb").close()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "10"}
        mock_resp.iter_content.return_value = [b"1234567890"]
        mock_get.return_value.__enter__.return_value = mock_resp

        result = start_download(
            url="https://example.com/frieren4.mkv",
            anime_name="Frieren",
            episode="4",
            filename="Frieren - Ep 4 [1080p].mkv",
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["filename"], "[SubsPlease] Frieren - 04 (1080p).mkv")

        task_id = result["task"]["task_id"]
        import time
        for _ in range(30):
            status = get_download_task(task_id)
            if status and status["status"] in ("completed", "error"):
                break
            time.sleep(0.05)

    def test_get_anime_local_episodes(self):
        for i in [1, 2, 3]:
            open(os.path.join(self.dest_folder, f"Mock_Anime_-_0{i}.mkv"), "wb").close()

        with patch("anibase.download_manager.find_matching_library_anime") as mock_find:
            mock_find.return_value = {
                "name": "Mock Anime",
                "path": self.dest_folder,
                "score": 1.0,
                "method": "exact",
            }
            res = get_anime_local_episodes("Mock Anime")
            self.assertTrue(res["matched"])
            self.assertEqual(res["existing_episodes"], [1, 2, 3])
            self.assertEqual(res["count"], 3)

    @patch("anibase.download_manager.get_samehadaku_download_links")
    def test_resolve_best_direct_link(self, mock_links):
        mock_links.return_value = [
            {
                "format": "MKV",
                "quality": "1080p",
                "links": [
                    {"host": "Mega", "url": "https://mega.nz/file/123", "is_direct": False, "is_pixeldrain": False},
                    {
                        "host": "Pixeldrain",
                        "url": "https://pixeldrain.com/u/abc12345",
                        "is_direct": True,
                        "is_pixeldrain": True,
                        "pixeldrain_id": "abc12345",
                        "direct_download_url": "https://pixeldrain.com/api/file/abc12345?download",
                    },
                ],
            }
        ]
        link = resolve_best_direct_link("https://example.com/ep1", preferred_quality="1080p")
        self.assertIsNotNone(link)
        self.assertEqual(link["direct_url"], "https://pixeldrain.com/api/file/abc12345?download")
        self.assertEqual(link["quality"], "1080p")

    @patch("anibase.download_manager.resolve_best_direct_link")
    @patch("anibase.download_manager.requests.get")
    def test_start_batch_download_and_worker(self, mock_get, mock_resolve):
        mock_resolve.return_value = {
            "direct_url": "https://example.com/video.mkv",
            "format": "MKV",
            "quality": "1080p",
            "host": "Pixeldrain",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "10"}
        mock_resp.iter_content.return_value = [b"1234567890"]
        mock_get.return_value.__enter__.return_value = mock_resp

        episodes = [
            {"episode": "1", "url": "https://example.com/ep1", "title": "Ep 1"},
            {"episode": "2", "url": "https://example.com/ep2", "title": "Ep 2"},
        ]
        result = start_batch_download(
            anime_name="Batch Anime",
            episodes=episodes,
            preferred_quality="1080p",
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        batch_id = result["batch"]["batch_id"]

        import time
        for _ in range(60):
            b = get_batch_task(batch_id)
            if b and b["status"] in ("completed", "cancelled", "error"):
                break
            time.sleep(0.05)

        final_batch = get_batch_task(batch_id)
        self.assertEqual(final_batch["status"], "completed")
        self.assertEqual(final_batch["completed_count"], 2)

    def test_cancel_batch_download(self):
        episodes = [
            {"episode": "1", "url": "https://example.com/ep1"},
            {"episode": "2", "url": "https://example.com/ep2"},
        ]
        result = start_batch_download(
            anime_name="Cancel Batch Anime",
            episodes=episodes,
            custom_dest_dir=self.dest_folder
        )
        batch_id = result["batch"]["batch_id"]
        cancelled = cancel_batch_download(batch_id)
        self.assertTrue(cancelled)
        batch = get_batch_task(batch_id)
        self.assertEqual(batch["status"], "cancelled")

    @patch("anibase.download_manager.requests.get")
    def test_http_range_resume_206(self, mock_get):
        """Uji kemampuan resume dengan HTTP 206 Partial Content saat berkas .part sudah ada."""
        filename = "resume_test_206.mkv"
        temp_part = os.path.join(self.dest_folder, f"{filename}.part")
        final_dest = os.path.join(self.dest_folder, filename)

        # Siapkan berkas .part dengan 5 byte awal
        with open(temp_part, "wb") as f:
            f.write(b"12345")

        mock_resp = MagicMock()
        mock_resp.status_code = 206
        mock_resp.headers = {
            "Content-Range": "bytes 5-9/10",
            "Content-Length": "5",
        }
        mock_resp.iter_content.return_value = [b"67890"]
        mock_get.return_value.__enter__.return_value = mock_resp

        result = start_download(
            url="https://example.com/video.mkv",
            anime_name="Resume Anime",
            episode="1",
            filename=filename,
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        task_id = result["task"]["task_id"]

        import time
        for _ in range(40):
            t = get_download_task(task_id)
            if t and t["status"] in ("completed", "error", "cancelled"):
                break
            time.sleep(0.05)

        task = get_download_task(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertTrue(task.get("is_resumed"))
        self.assertEqual(task["total_bytes"], 10)
        self.assertEqual(task["downloaded_bytes"], 10)

        # Pastikan berkas akhir tersambung sempurna (10 byte)
        with open(final_dest, "rb") as f:
            content = f.read()
        self.assertEqual(content, b"1234567890")

    @patch("anibase.download_manager.requests.get")
    def test_http_range_fallback_200(self, mock_get):
        """Uji fallback jika server menolak Range dan mengembalikan 200 OK (tulis ulang dari 0)."""
        filename = "fallback_test_200.mkv"
        temp_part = os.path.join(self.dest_folder, f"{filename}.part")
        final_dest = os.path.join(self.dest_folder, filename)

        with open(temp_part, "wb") as f:
            f.write(b"junk_data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "10"}
        mock_resp.iter_content.return_value = [b"abcdefghij"]
        mock_get.return_value.__enter__.return_value = mock_resp

        result = start_download(
            url="https://example.com/video2.mkv",
            anime_name="Fallback Anime",
            episode="2",
            filename=filename,
            custom_dest_dir=self.dest_folder
        )
        self.assertTrue(result["ok"])
        task_id = result["task"]["task_id"]

        import time
        for _ in range(40):
            t = get_download_task(task_id)
            if t and t["status"] in ("completed", "error", "cancelled"):
                break
            time.sleep(0.05)

        task = get_download_task(task_id)
        self.assertEqual(task["status"], "completed")
        with open(final_dest, "rb") as f:
            content = f.read()
        self.assertEqual(content, b"abcdefghij")

    def test_pause_and_resume_download_lifecycle(self):
        """Uji siklus jeda dan lanjut unduhan."""
        import threading
        ready_evt = threading.Event()
        proceed_evt = threading.Event()

        def slow_iter():
            ready_evt.set()
            # Tunggu jeda dipicu
            proceed_evt.wait(timeout=2.0)
            yield b"12345"

        with patch("anibase.download_manager.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"Content-Length": "5"}
            mock_resp.iter_content.return_value = slow_iter()
            mock_get.return_value.__enter__.return_value = mock_resp

            result = start_download(
                url="https://example.com/slow.mkv",
                anime_name="Slow Anime",
                episode="3",
                filename="slow.mkv",
                custom_dest_dir=self.dest_folder
            )
            task_id = result["task"]["task_id"]
            ready_evt.wait(timeout=1.0)

            # Jeda unduhan
            paused = pause_download(task_id)
            self.assertTrue(paused)
            proceed_evt.set()

            import time
            time.sleep(0.1)
            task = get_download_task(task_id)
            self.assertEqual(task["status"], "paused")

            # Lanjutkan kembali
            resumed = resume_download(task_id)
            self.assertTrue(resumed)
            task_after = get_download_task(task_id)
            self.assertIn(task_after["status"], ("queued", "downloading", "completed"))

            # Batalkan agar tidak menggantung dan beri jeda penutupan file handle
            cancel_download(task_id)
            import time
            time.sleep(0.2)

    def test_queue_concurrency_settings(self):
        """Uji getter/setter batas antrean paralel."""
        orig = get_max_concurrent_downloads()
        try:
            set_max_concurrent_downloads(4, save=False)
            self.assertEqual(get_max_concurrent_downloads(), 4)
            overview = get_queue_overview()
            self.assertEqual(overview["max_concurrent"], 4)
        finally:
            set_max_concurrent_downloads(orig, save=False)


class SamehadakuApiRoutesTests(unittest.TestCase):
    def setUp(self):
        self.setup_patcher = patch("anibase.web.helpers.is_setup_complete", return_value=True)
        self.setup_patcher.start()
        self.addCleanup(self.setup_patcher.stop)
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
        res = self.client.get("/api/samehadaku/episodes?url=https://v2.samehadaku.how/anime/frieren/")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

    @patch("anibase.web.routes_api.get_samehadaku_download_links")
    def test_api_samehadaku_downloads(self, mock_links):
        mock_links.return_value = [{"format": "MKV", "quality": "1080p", "links": []}]
        res = self.client.get("/api/samehadaku/downloads?url=https://v2.samehadaku.how/frieren-episode-1/")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["downloads"]), 1)

    @patch("anibase.integrations.samehadaku.requests.get")
    def test_samehadaku_ssrf_and_disallowed_host_rejected(self, mock_get):
        for endpoint in ("/api/samehadaku/episodes", "/api/samehadaku/downloads"):
            for bad_url in (
                "http://127.0.0.1:5000/settings",
                "http://169.254.169.254/latest/meta-data/",
                "https://evil.example/fake",
                "file:///etc/passwd",
            ):
                res = self.client.get(f"{endpoint}?url={bad_url}")
                self.assertEqual(res.status_code, 400)
                self.assertFalse(res.get_json()["ok"])
        self.assertFalse(get_samehadaku_anime_details("https://evil.example/anime").get("ok"))
        self.assertEqual(get_samehadaku_download_links("http://127.0.0.1/ep1"), [])
        mock_get.assert_not_called()

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

    @patch("anibase.web.routes_api.get_anime_local_episodes")
    @patch("anibase.web.routes_api.get_samehadaku_anime_details")
    def test_api_samehadaku_episodes_enriches_local_library(self, mock_details, mock_local):
        mock_details.return_value = {
            "ok": True,
            "title": "Sousou no Frieren",
            "episodes": [
                {"episode": "1", "title": "Ep 1", "url": "https://example.com/1"},
                {"episode": "2", "title": "Ep 2", "url": "https://example.com/2"},
            ]
        }
        mock_local.return_value = {
            "matched": True,
            "folder": "/mock/frieren",
            "anime_name": "Sousou no Frieren",
            "existing_episodes": [1],
            "count": 1,
        }
        res = self.client.get("/api/samehadaku/episodes?url=https://v2.samehadaku.how/anime/frieren/")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["local_library"]["matched"])
        self.assertTrue(data["episodes"][0]["is_local"])
        self.assertFalse(data["episodes"][1]["is_local"])
        self.assertEqual(len(data["missing_episodes"]), 1)
        self.assertEqual(data["missing_episodes"][0]["episode"], "2")

    @patch("anibase.web.routes_api.start_batch_download")
    def test_api_samehadaku_start_batch_download(self, mock_start):
        from anibase.settings import get_action_token
        token = get_action_token()
        mock_start.return_value = {"ok": True, "batch": {"batch_id": "batch_123"}}
        res = self.client.post(
            "/api/samehadaku/download/batch",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={
                "anime_name": "Test Anime",
                "episodes": [{"episode": "1", "url": "https://example.com/1"}],
                "preferred_quality": "1080p",
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["batch"]["batch_id"], "batch_123")

    def test_api_samehadaku_batch_status(self):
        res = self.client.get("/api/samehadaku/download/batch/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["batches"], list)

    def test_api_download_status_includes_queue_info(self):
        res = self.client.get("/api/samehadaku/download/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("queue_info", data)
        self.assertIn("max_concurrent", data["queue_info"])
        self.assertIn("active_count", data["queue_info"])

    @patch("anibase.web.routes_api.pause_download")
    @patch("anibase.web.routes_api.resume_download")
    def test_api_pause_and_resume_download(self, mock_resume, mock_pause):
        from anibase.settings import get_action_token
        token = get_action_token()
        mock_pause.return_value = True
        mock_resume.return_value = True

        # Pause
        res_pause = self.client.post(
            "/api/samehadaku/download/pause",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={"task_id": "dl_test_pause"}
        )
        self.assertEqual(res_pause.status_code, 200)
        data_pause = res_pause.get_json()
        self.assertTrue(data_pause["ok"])
        self.assertTrue(data_pause["paused"])

        # Resume
        res_resume = self.client.post(
            "/api/samehadaku/download/resume",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={"task_id": "dl_test_pause"}
        )
        self.assertEqual(res_resume.status_code, 200)
        data_resume = res_resume.get_json()
        self.assertTrue(data_resume["ok"])
        self.assertTrue(data_resume["resumed"])

    def test_api_queue_settings(self):
        from anibase.settings import get_action_token
        token = get_action_token()

        res = self.client.post(
            "/api/samehadaku/download/queue/settings",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={"max_concurrent": 3}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["max_concurrent"], 3)
        self.assertEqual(data["queue_info"]["max_concurrent"], 3)


if __name__ == "__main__":
    unittest.main()
