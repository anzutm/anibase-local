"""Unit tests for Lendrive scraper and direct link resolver."""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from anibase.download_manager import (
    adapt_filename_to_library_pattern,
    start_download,
    cancel_download,
    resolve_best_direct_link,
    _download_worker,
    _DOWNLOAD_TASKS,
    _TASKS_LOCK,
)
from anibase.integrations.lendrive import (
    clean_lendrive_title,
    extract_lendrive_episode_number,
    search_lendrive,
    get_lendrive_latest_episodes,
    get_lendrive_anime_details,
    get_lendrive_download_links,
    resolve_lendrive_best_direct_link,
    get_lendrive_base_url,
    _parse_lendrive_link_elements,
)


SAMPLE_LENDRIVE_SEARCH_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="post-show">
    <article id="post-101" class="post-item type-post">
        <a href="https://lendrive.web.id/doumo-suki-na-hito-sub-indo/" title="[LENDRIVE] Doumo, Suki na Hito ni Horegusuri wo Irai sareta Majo desu. [HEVC][Dual Subs]">
            <div class="thumb">
                <img src="https://lendrive.web.id/wp-content/uploads/doumo.jpg" alt="Doumo" />
            </div>
            <h2 class="entry-title">[LENDRIVE] Doumo, Suki na Hito ni Horegusuri wo Irai sareta Majo desu. [HEVC][Dual Subs]</h2>
        </a>
    </article>
    <article id="post-102" class="post-item type-post">
        <a href="https://lendrive.web.id/doumo-suki-ep-07/" title="[LENDRIVE] Doumo Suki - 07 [1080p HEVC][Dualsubs]">
            <div class="thumb">
                <img src="https://lendrive.web.id/wp-content/uploads/ep7.jpg" alt="Ep 7" />
            </div>
            <h2 class="entry-title">[LENDRIVE] Doumo Suki - 07 [1080p HEVC][Dualsubs]</h2>
        </a>
    </article>
</div>
</body>
</html>
"""

SAMPLE_LENDRIVE_DETAIL_HTML = """
<!DOCTYPE html>
<html>
<body>
<article class="post">
    <h1 class="entry-title">[LENDRIVE] Doumo, Suki na Hito ni Horegusuri wo Irai sareta Majo desu. [HEVC][Dual Subs]</h1>
    <div class="poster"><img src="https://lendrive.web.id/wp-content/uploads/poster.jpg" /></div>
    <div class="synopsis">Rose adalah seorang penyihir muda yang diminta membuat ramuan cinta oleh orang yang disukainya.</div>
    <div class="download-box">
        <ul class="episodelist">
            <li><a href="https://lendrive.web.id/doumo-suki-ep-01/">Episode 01 [Dual Subs]</a></li>
            <li><a href="https://lendrive.web.id/doumo-suki-ep-02/">Episode 02 [Dual Subs]</a></li>
            <li><a href="https://lendrive.web.id/doumo-suki-ep-03/">Episode 03 [Dual Subs]</a></li>
        </ul>
    </div>
</article>
</body>
</html>
"""

SAMPLE_LENDRIVE_DOWNLOAD_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="entry-content">
    <div class="download-box">
        <strong>1080p HEVC DualSubs</strong>
        <a href="https://pixeldrain.com/u/abc12345">Pixeldrain</a>
        <a href="https://www.mediafire.com/file/xyz987/video.mkv">Mediafire</a>
        <a href="https://gofile.io/d/sample123">Gofile</a>

        <strong>720p HEVC DualSubs</strong>
        <a href="https://pixeldrain.com/u/def67890">Pixeldrain</a>
        <a href="https://www.mediafire.com/file/720p/video.mkv">Mediafire</a>

        <strong>480p HEVC</strong>
        <a href="https://pixeldrain.com/u/ghi456">Pixeldrain</a>
    </div>
</div>
</body>
</html>
"""


class LendriveIntegrationTests(unittest.TestCase):

    def setUp(self):
        from anibase.integrations.lendrive import clear_lendrive_cache
        clear_lendrive_cache()

    def tearDown(self):
        from anibase.integrations.lendrive import clear_lendrive_cache
        clear_lendrive_cache()

    def test_clean_lendrive_title(self):
        self.assertEqual(
            clean_lendrive_title("[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs]"),
            "Doumo Suki"
        )
        self.assertEqual(
            clean_lendrive_title("Psyren – Ep 06 (Dual subs) x265/HEVC"),
            "Psyren"
        )
        self.assertEqual(
            clean_lendrive_title("Kanojo no Tomodachi (uncen) - Ep 07 (x265 HEVC (DualSubs))"),
            "Kanojo no Tomodachi (uncen)"
        )
        self.assertEqual(
            clean_lendrive_title("[LENDRIVE] Sousou no Frieren [Batch][x265 HEVC]"),
            "Sousou no Frieren"
        )
        self.assertEqual(
            clean_lendrive_title("Kanojo Okarishimasu Dual Subs Sub Indo"),
            "Kanojo Okarishimasu"
        )

    def test_extract_lendrive_episode_number(self):
        self.assertEqual(extract_lendrive_episode_number("[LENDRIVE] Doumo Suki - 02 [1080p HEVC]"), "2")
        self.assertEqual(extract_lendrive_episode_number("Doumo Suki - Ep 07 [1080p]"), "7")
        self.assertEqual(extract_lendrive_episode_number("Episode 12"), "12")
        self.assertEqual(extract_lendrive_episode_number("Episode 24.5"), "24.5")
        self.assertEqual(extract_lendrive_episode_number("https://lendrive.web.id/anime-ep-05/"), "5")

    def test_get_lendrive_base_url_default(self):
        url = get_lendrive_base_url()
        self.assertTrue(url.startswith("http"))
        self.assertFalse(url.endswith("/"))

    @patch("anibase.integrations.lendrive.requests.get")
    def test_search_lendrive(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_LENDRIVE_SEARCH_HTML
        mock_get.return_value = mock_resp

        results = search_lendrive("Doumo Suki")
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 2)

        first = results[0]
        self.assertIn("Doumo", first["title"])
        self.assertIn("lendrive.web.id", first["url"])
        self.assertEqual(first["provider"], "lendrive")
        self.assertIn("HEVC", first["format_badge"])

        second = results[1]
        self.assertEqual(second["episode_number"], "7")
        self.assertEqual(second["episode"], "Episode 7")

    @patch("anibase.integrations.lendrive.requests.get")
    def test_get_lendrive_latest_episodes(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_LENDRIVE_SEARCH_HTML
        mock_get.return_value = mock_resp

        results = get_lendrive_latest_episodes(page=1)
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]["provider"], "lendrive")

        # Second call without force_refresh uses cache
        call_count_before = mock_get.call_count
        cached_results = get_lendrive_latest_episodes(page=1, force_refresh=False)
        self.assertEqual(mock_get.call_count, call_count_before)
        self.assertEqual(len(cached_results), len(results))

        # Call with force_refresh=True makes a fresh request
        refreshed_results = get_lendrive_latest_episodes(page=1, force_refresh=True)
        self.assertEqual(mock_get.call_count, call_count_before + 1)
        self.assertEqual(len(refreshed_results), len(results))

    @patch("anibase.integrations.lendrive.requests.get")
    def test_get_lendrive_latest_episodes_homepage_section_and_thumbnails(self, mock_get):
        homepage_html = """
        <div class="releases hothome"><h3>Popular Today</h3></div>
        <div class="listupd">
            <article class="bs"><div class="bsx"><a href="https://lendrive.web.id/popular-ep-01/"><div class="tt">Popular</div><div class="limit"><img src="https://lendrive.web.id/pop.jpg" /></div></a></div></article>
        </div>
        <div class="bixbox bbnofrm">
            <div class="releases latesthome"><h3>Update x265/HEVC</h3><a class="vl" href="https://lendrive.web.id/anime/?status=&type=&order=update">View All</a></div>
            <div class="listupd normal">
                <article class="bs styletere">
                    <div class="bsx">
                        <a href="https://lendrive.web.id/psyren-ep-06-dual-subs-x265-hevc-subtitle-indonesia-english/" title="Psyren – Ep 06 (Dual subs) x265/HEVC Subtitle Indonesia &#038; English">
                            <div class="tt">Psyren</div>
                            <div class="limit">
                                <img src="https://lendrive.web.id/wp-content/uploads/2026/11/psyren-768x432.jpg" />
                            </div>
                            <div class="epdate">
                                <span class="epx">Ep 6</span>
                                <span class="datex">22 hours ago</span>
                            </div>
                        </a>
                    </div>
                </article>
                <article class="bs styletere">
                    <div class="bsx">
                        <a href="https://lendrive.web.id/tensei-goblin-dakedo-shitsumon-aru-ep-07-dual-subs-x265-hevc-subtitle-indonesia-english/" title="Tensei Goblin dakedo Shitsumon Aru? – Ep 07 (Dual subs) x265/HEVC Subtitle Indonesia &#038; English">
                            <div class="tt">Tensei Goblin dakedo Shitsumon Aru?</div>
                            <div class="limit">
                                <img src="https://lendrive.web.id/wp-content/uploads/2026/11/goblin-768x432.jpg" />
                            </div>
                            <div class="epdate">
                                <span class="epx">Ep 7</span>
                                <span class="datex">23 hours ago</span>
                            </div>
                        </a>
                    </div>
                </article>
            </div>
        </div>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = homepage_html
        mock_get.return_value = mock_resp

        # Clear any cached latest releases
        from anibase.integrations.lendrive import clear_lendrive_cache
        clear_lendrive_cache()

        results = get_lendrive_latest_episodes(page=1)
        self.assertEqual(len(results), 2)
        # Urutan harus sesuai Update x265/HEVC (Psyren pertama, Tensei Goblin kedua)
        self.assertEqual(results[0]["title"], "Psyren")
        self.assertEqual(results[0]["thumbnail"], "https://lendrive.web.id/wp-content/uploads/2026/11/psyren-768x432.jpg")
        self.assertEqual(results[0]["episode_number"], "6")
        self.assertEqual(results[0]["released_on"], "22 hours ago")

        self.assertEqual(results[1]["title"], "Tensei Goblin dakedo Shitsumon Aru?")
        self.assertEqual(results[1]["thumbnail"], "https://lendrive.web.id/wp-content/uploads/2026/11/goblin-768x432.jpg")
        self.assertEqual(results[1]["episode_number"], "7")
        self.assertEqual(results[1]["released_on"], "23 hours ago")

    @patch("anibase.integrations.lendrive.requests.get")
    def test_get_lendrive_anime_details(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_LENDRIVE_DETAIL_HTML
        mock_get.return_value = mock_resp

        details = get_lendrive_anime_details("https://lendrive.web.id/doumo-suki-na-hito-sub-indo/")
        self.assertTrue(details["ok"])
        self.assertIn("Doumo", details["title"])
        self.assertIn("Rose", details["synopsis"])
        self.assertTrue(details["is_hevc"])
        self.assertTrue(details["is_dualsubs"])
        self.assertEqual(details["provider"], "lendrive")

        # Episodes
        self.assertEqual(len(details["episodes"]), 3)
        self.assertEqual(details["episodes"][0]["episode"], "3")
        self.assertEqual(details["episodes"][1]["episode"], "2")
        self.assertEqual(details["episodes"][2]["episode"], "1")

    @patch("anibase.integrations.lendrive.requests.get")
    def test_get_lendrive_download_links(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_LENDRIVE_DOWNLOAD_HTML
        mock_get.return_value = mock_resp

        dls = get_lendrive_download_links("https://lendrive.web.id/doumo-suki-ep-01/")
        self.assertIsInstance(dls, list)
        self.assertEqual(len(dls), 3)

        # 1080p
        dl_1080 = dls[0]
        self.assertEqual(dl_1080["quality"], "1080p")
        self.assertTrue(dl_1080["is_hevc"])
        self.assertTrue(dl_1080["is_dualsubs"])
        self.assertEqual(len(dl_1080["links"]), 3)

        # Pixeldrain direct detection
        pd_link = dl_1080["links"][0]
        self.assertEqual(pd_link["host"], "Pixeldrain")
        self.assertTrue(pd_link["is_direct"])
        self.assertEqual(pd_link["pixeldrain_id"], "abc12345")
        self.assertEqual(pd_link["direct_download_url"], "https://pixeldrain.com/api/file/abc12345?download")

        # Mediafire detection
        mf_link = dl_1080["links"][1]
        self.assertEqual(mf_link["host"], "Mediafire")
        self.assertTrue(mf_link["is_mediafire"])

    @patch("anibase.integrations.lendrive.get_lendrive_download_links")
    def test_resolve_lendrive_best_direct_link_pixeldrain(self, mock_dls):
        mock_dls.return_value = [
            {
                "quality": "1080p",
                "format": "MKV (x265 HEVC)",
                "is_hevc": True,
                "is_dualsubs": True,
                "links": [
                    {
                        "host": "Pixeldrain",
                        "url": "https://pixeldrain.com/u/toplink",
                        "is_direct": True,
                        "is_pixeldrain": True,
                        "pixeldrain_id": "toplink",
                        "direct_download_url": "https://pixeldrain.com/api/file/toplink?download"
                    }
                ]
            }
        ]

        best = resolve_lendrive_best_direct_link("https://lendrive.web.id/ep1", preferred_quality="1080p")
        self.assertIsNotNone(best)
        self.assertEqual(best["direct_url"], "https://pixeldrain.com/api/file/toplink?download")
        self.assertEqual(best["host"], "Pixeldrain")
        self.assertEqual(best["quality"], "1080p")
        self.assertTrue(best["is_hevc"])
        self.assertTrue(best["is_dualsubs"])


class LendriveApiRoutesTests(unittest.TestCase):
    def setUp(self):
        from anibase.web import create_app
        self.setup_patcher = patch("anibase.web.helpers.is_setup_complete", return_value=True)
        self.setup_patcher.start()
        self.addCleanup(self.setup_patcher.stop)
        self.app = create_app()
        self.client = self.app.test_client()

    @patch("anibase.web.routes_api.search_lendrive")
    def test_api_lendrive_search(self, mock_search):
        mock_search.return_value = [{"title": "Doumo Suki", "url": "https://lendrive.web.id/doumo"}]
        res = self.client.get("/api/lendrive/search?q=doumo")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["title"], "Doumo Suki")

    @patch("anibase.web.routes_api.get_lendrive_latest_episodes")
    def test_api_lendrive_latest(self, mock_latest):
        mock_latest.return_value = [{"title": "Doumo Ep 7", "url": "https://lendrive.web.id/ep7"}]
        res = self.client.get("/api/lendrive/latest?page=1")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["page"], 1)

    @patch("anibase.web.routes_api.get_anime_local_episodes")
    @patch("anibase.web.routes_api.get_lendrive_anime_details")
    def test_api_lendrive_episodes(self, mock_details, mock_local):
        mock_details.return_value = {
            "ok": True,
            "title": "Doumo Suki",
            "episodes": [
                {"episode": "1", "title": "Episode 1", "url": "https://lendrive.web.id/ep1"},
                {"episode": "2", "title": "Episode 2", "url": "https://lendrive.web.id/ep2"},
            ]
        }
        mock_local.return_value = {
            "matched": True,
            "folder": "/mock/doumo",
            "anime_name": "Doumo Suki",
            "existing_episodes": [1],
            "count": 1,
        }
        res = self.client.get("/api/lendrive/episodes?url=https://lendrive.web.id/doumo")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["local_library"]["matched"])
        self.assertTrue(data["episodes"][0]["is_local"])
        self.assertFalse(data["episodes"][1]["is_local"])
        self.assertEqual(len(data["missing_episodes"]), 1)
        self.assertEqual(data["missing_episodes"][0]["episode"], "2")

    @patch("anibase.web.routes_api.get_lendrive_download_links")
    def test_api_lendrive_downloads(self, mock_links):
        mock_links.return_value = [
            {"format": "MKV (x265 HEVC)", "quality": "1080p", "links": []}
        ]
        res = self.client.get("/api/lendrive/downloads?url=https://lendrive.web.id/ep1")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["downloads"]), 1)

    @patch("anibase.web.routes_api.start_download")
    def test_api_lendrive_start_download(self, mock_start):
        from anibase.settings import get_action_token
        token = get_action_token()
        mock_start.return_value = {
            "ok": True,
            "task": {"task_id": "dl_lendrive123", "provider": "lendrive"}
        }

        res = self.client.post(
            "/api/lendrive/download",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={
                "url": "https://pixeldrain.com/api/file/abc12345?download",
                "anime_name": "Doumo Suki",
                "episode": "2",
                "filename": "[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs].mkv",
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["task"]["provider"], "lendrive")
        # Pastikan dipanggil dengan provider="lendrive"
        mock_start.assert_called_once_with(
            url="https://pixeldrain.com/api/file/abc12345?download",
            anime_name="Doumo Suki",
            episode="2",
            filename="[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs].mkv",
            custom_dest_dir="",
            provider="lendrive"
        )

    @patch("anibase.web.routes_api.start_batch_download")
    def test_api_lendrive_batch_download(self, mock_batch):
        from anibase.settings import get_action_token
        token = get_action_token()
        mock_batch.return_value = {"ok": True, "batch": {"batch_id": "batch_lendrive_123"}}
        res = self.client.post(
            "/api/lendrive/download/batch",
            headers={"X-Action-Token": token, "Content-Type": "application/json"},
            json={
                "anime_name": "Doumo Suki",
                "episodes": [{"episode": "1", "url": "https://lendrive.web.id/ep1"}],
                "preferred_quality": "1080p"
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["batch"]["batch_id"], "batch_lendrive_123")


class LendriveFilenameAndDownloadManagerTests(unittest.TestCase):
    def test_resolve_best_direct_link_routing_lendrive(self):
        with patch("anibase.integrations.lendrive.resolve_lendrive_best_direct_link") as mock_res:
            mock_res.return_value = {
                "direct_url": "https://pixeldrain.com/api/file/xyz123?download",
                "host": "Pixeldrain",
                "quality": "1080p",
                "format": "MKV",
                "is_direct": True,
            }
            res = resolve_best_direct_link("https://lendrive.web.id/doumo-suki-ep-01/", preferred_quality="1080p")
            self.assertIsNotNone(res)
            self.assertEqual(res["provider"], "lendrive")
            self.assertEqual(res["direct_url"], "https://pixeldrain.com/api/file/xyz123?download")
            mock_res.assert_called_once_with("https://lendrive.web.id/doumo-suki-ep-01/", preferred_quality="1080p")
    def test_adapt_filename_to_existing_lendrive_pattern(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create existing Lendrive episode file
            ep1_file = os.path.join(tmp_dir, "[LENDRIVE] Doumo Suki - 01 [1080p HEVC][Dualsubs].mkv")
            with open(ep1_file, "w") as f:
                f.write("mock")

            # Downloading episode 2
            adapted = adapt_filename_to_library_pattern(
                folder_path=tmp_dir,
                episode="2",
                default_filename="[LENDRIVE] Doumo Suki - 02 [1080p HEVC][DualSubs].mkv"
            )
            self.assertEqual(adapted, "[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs].mkv")

    def test_adapt_filename_from_lendrive_to_standard_library_pattern(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # User library uses standard pattern: Doumo Suki - Ep 01 [1080p].mkv
            ep1_file = os.path.join(tmp_dir, "Doumo Suki - Ep 01 [1080p].mkv")
            with open(ep1_file, "w") as f:
                f.write("mock")

            # Downloading from Lendrive
            adapted = adapt_filename_to_library_pattern(
                folder_path=tmp_dir,
                episode="2",
                default_filename="[LENDRIVE] Doumo Suki - 02 [1080p HEVC][DualSubs].mkv"
            )
            # Should adapt prefix and suffix to match existing user files
            self.assertEqual(adapted, "Doumo Suki - Ep 02 [1080p].mkv")

    def test_adapt_filename_new_folder_keeps_lendrive_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Empty folder: should preserve default Lendrive filename
            adapted = adapt_filename_to_library_pattern(
                folder_path=tmp_dir,
                episode="1",
                default_filename="[LENDRIVE] Doumo Suki - 01 [1080p HEVC][DualSubs].mkv"
            )
            self.assertEqual(adapted, "[LENDRIVE] Doumo Suki - 01 [1080p HEVC][DualSubs].mkv")

    @patch("anibase.download_manager._download_worker")
    def test_start_download_with_lendrive_provider(self, mock_worker):
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = start_download(
                url="https://pixeldrain.com/api/file/abc12345?download",
                anime_name="Doumo Suki",
                episode="1",
                filename="[LENDRIVE] Doumo Suki - 01 [1080p HEVC][Dualsubs].mkv",
                custom_dest_dir=tmp_dir,
                provider="lendrive"
            )
            self.assertTrue(res["ok"])
            task = res["task"]
            self.assertEqual(task["provider"], "lendrive")
            self.assertIn("[LENDRIVE] Doumo Suki - 01", task["filename"])
            cancel_download(task["task_id"])

    def test_parse_lendrive_link_elements_safelink(self):
        html_snippet = '''
        <a href="https://sfe.ad/9xGDmrKxW9">Mediafire</a>
        <a href="https://sfe.ad/mirror123">Acefile</a>
        <a href="https://pixeldrain.com/u/direct456">Pixeldrain</a>
        '''
        links = _parse_lendrive_link_elements(html_snippet)
        self.assertEqual(len(links), 3)

        # Mediafire wrapped in sfe.ad safelink
        mf = links[0]
        self.assertEqual(mf["host"], "Mediafire")
        self.assertTrue(mf["is_safelink"])
        self.assertFalse(mf["is_mediafire"])
        self.assertFalse(mf["is_direct"])
        self.assertIsNone(mf["direct_download_url"])

        # Acefile wrapped in sfe.ad safelink
        af = links[1]
        self.assertEqual(af["host"], "Acefile")
        self.assertTrue(af["is_safelink"])
        self.assertFalse(af["is_direct"])

        # Real Pixeldrain
        pd = links[2]
        self.assertEqual(pd["host"], "Pixeldrain")
        self.assertFalse(pd["is_safelink"])
        self.assertTrue(pd["is_direct"])
        self.assertEqual(pd["direct_download_url"], "https://pixeldrain.com/api/file/direct456?download")

    def test_start_download_rejects_safelink_url(self):
        res = start_download(
            url="https://sfe.ad/9xGDmrKxW9",
            anime_name="Doumo Suki",
            episode="7",
            filename="[LENDRIVE] Doumo Suki - 07 [1080p HEVC][DualSubs].mkv",
            provider="lendrive"
        )
        self.assertFalse(res["ok"])
        self.assertIn("safelink", res["error"].lower())

    @patch("anibase.download_manager.requests.get")
    def test_download_worker_aborts_on_html_content_type(self, mock_get):
        import threading
        task_id = "dl_test_html_guard"
        with _TASKS_LOCK:
            _DOWNLOAD_TASKS[task_id] = {
                "task_id": task_id,
                "status": "queued",
                "downloaded_bytes": 0,
                "total_bytes": 0,
            }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=UTF-8"}
        mock_resp.__enter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            save_path = os.path.join(tmp_dir, "test.mkv")
            temp_path = f"{save_path}.part"
            cancel_ev = threading.Event()

            _download_worker(
                task_id=task_id,
                url="https://sfe.ad/dummy",
                save_path=save_path,
                temp_path=temp_path,
                cancel_event=cancel_ev
            )

            # Task status must be error, NOT completed
            with _TASKS_LOCK:
                task = _DOWNLOAD_TASKS.get(task_id)
                self.assertEqual(task["status"], "error")
                self.assertIn("HTML", task["error"])

            # Corrupt files must not be left behind
            self.assertFalse(os.path.exists(save_path))
            self.assertFalse(os.path.exists(temp_path))

    @patch("anibase.download_manager.requests.get")
    def test_download_worker_aborts_on_html_chunk_bytes(self, mock_get):
        import threading
        task_id = "dl_test_chunk_guard"
        with _TASKS_LOCK:
            _DOWNLOAD_TASKS[task_id] = {
                "task_id": task_id,
                "status": "queued",
                "downloaded_bytes": 0,
                "total_bytes": 0,
            }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Headers might disguise content-type as octet-stream
        mock_resp.headers = {"Content-Type": "application/octet-stream", "Content-Length": "27000"}
        mock_resp.iter_content.return_value = [b"<!DOCTYPE html><html><head><title>Safelink</title></head><body>"]
        mock_resp.__enter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            save_path = os.path.join(tmp_dir, "test2.mkv")
            temp_path = f"{save_path}.part"
            cancel_ev = threading.Event()

            _download_worker(
                task_id=task_id,
                url="https://example.com/fakevideo.mkv",
                save_path=save_path,
                temp_path=temp_path,
                cancel_event=cancel_ev
            )

            with _TASKS_LOCK:
                task = _DOWNLOAD_TASKS.get(task_id)
                self.assertEqual(task["status"], "error")
                self.assertIn("HTML", task["error"])

            self.assertFalse(os.path.exists(save_path))
            self.assertFalse(os.path.exists(temp_path))

    @patch("anibase.download_manager.requests.get")
    def test_download_worker_aborts_on_html_body_tag(self, mock_get):
        import threading
        task_id = "dl_test_body_guard"
        with _TASKS_LOCK:
            _DOWNLOAD_TASKS[task_id] = {
                "task_id": task_id,
                "status": "queued",
                "downloaded_bytes": 0,
                "total_bytes": 0,
            }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/octet-stream", "Content-Length": "1024"}
        mock_resp.iter_content.return_value = [b"<html lang='en'><head></head><body>Redirecting to safelink...</body></html>"]
        mock_resp.__enter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            save_path = os.path.join(tmp_dir, "tiny.mkv")
            temp_path = f"{save_path}.part"
            cancel_ev = threading.Event()

            _download_worker(
                task_id=task_id,
                url="https://example.com/tiny.mkv",
                save_path=save_path,
                temp_path=temp_path,
                cancel_event=cancel_ev
            )

            with _TASKS_LOCK:
                task = _DOWNLOAD_TASKS.get(task_id)
                self.assertEqual(task["status"], "error")
                self.assertIn("HTML", task["error"])

            self.assertFalse(os.path.exists(save_path))
            self.assertFalse(os.path.exists(temp_path))

    def test_custom_lendrive_mirror_detected_and_ssrf_blocked(self):
        from anibase.utils import is_lendrive_provider_url
        with patch("anibase.settings.load_settings", return_value={"lendrive_base_url": "https://mirror.lendrive.cc"}):
            self.assertTrue(is_lendrive_provider_url("https://mirror.lendrive.cc/ep-1/"))
            self.assertTrue(is_lendrive_provider_url("https://lendrive.web.id/ep-1/"))
            self.assertFalse(is_lendrive_provider_url("https://evil.example/ep-1/"))
            with patch("anibase.integrations.lendrive.resolve_lendrive_best_direct_link", return_value={"direct_url": "https://pixeldrain.com/api/file/abc?download"}) as mock_res:
                res = resolve_best_direct_link("https://mirror.lendrive.cc/ep-1/", preferred_quality="1080p")
                self.assertEqual(res["provider"], "lendrive")
                mock_res.assert_called_once()

        with patch("anibase.integrations.lendrive.requests.get") as mock_get:
            self.assertFalse(get_lendrive_anime_details("http://127.0.0.1/admin").get("ok"))
            self.assertEqual(get_lendrive_download_links("https://evil.example/ep-1/"), [])
            mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
