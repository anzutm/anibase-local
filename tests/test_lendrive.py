"""Unit tests for Lendrive scraper and direct link resolver."""

import os
import unittest
from unittest.mock import patch, MagicMock

from anibase.integrations.lendrive import (
    clean_lendrive_title,
    extract_lendrive_episode_number,
    search_lendrive,
    get_lendrive_latest_episodes,
    get_lendrive_anime_details,
    get_lendrive_download_links,
    resolve_lendrive_best_direct_link,
    get_lendrive_base_url,
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

    def test_clean_lendrive_title(self):
        self.assertEqual(
            clean_lendrive_title("[LENDRIVE] Doumo Suki - 02 [1080p HEVC][Dualsubs]"),
            "Doumo Suki - 02"
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


if __name__ == "__main__":
    unittest.main()
