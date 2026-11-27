"""Unit tests for Ongoing Watchlist Tracker & Episode Release Notifications."""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from anibase.db import (
    init_db,
    db_connection,
    get_ongoing_watchlist,
    upsert_ongoing_watchlist,
    update_ongoing_watchlist_check,
    set_ongoing_watchlist_tracked,
    delete_ongoing_watchlist,
    save_episode_notification,
    get_episode_notifications,
    mark_notification_read,
    mark_notification_downloaded,
    get_unread_notification_count,
)
from anibase.watchlist_tracker import (
    extract_episode_number,
    scan_ongoing_anime,
    check_ongoing_releases,
    on_download_completed,
    quick_download_episode_alert,
)
from anibase.web import create_app
from anibase.settings import get_action_token


class TestWatchlistTrackerDB(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_anibase.db")
        init_db(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_upsert_and_get_ongoing_watchlist(self):
        with db_connection(self.db_path) as conn:
            # 1. Upsert initial
            res = upsert_ongoing_watchlist(
                anime_name="Psyren",
                samehadaku_url="https://v2.samehadaku.how/anime/psyren/",
                is_tracked=1,
                source="ongoing_folder",
                conn=conn
            )
            self.assertTrue(res)

            watchlist = get_ongoing_watchlist(active_only=True, conn=conn)
            self.assertEqual(len(watchlist), 1)
            self.assertEqual(watchlist[0]["anime_name"], "Psyren")
            self.assertEqual(watchlist[0]["source"], "ongoing_folder")
            self.assertTrue(watchlist[0]["is_tracked"])

            # 2. Update tracking state
            set_ongoing_watchlist_tracked("Psyren", False, conn=conn)
            active_list = get_ongoing_watchlist(active_only=True, conn=conn)
            self.assertEqual(len(active_list), 0)

            all_list = get_ongoing_watchlist(active_only=False, conn=conn)
            self.assertEqual(len(all_list), 1)
            self.assertFalse(all_list[0]["is_tracked"])

            # 3. Update check stats
            update_ongoing_watchlist_check("Psyren", latest_samehadaku_ep=6, last_known_episode=5, conn=conn)
            updated = get_ongoing_watchlist(active_only=False, conn=conn)[0]
            self.assertEqual(updated["latest_samehadaku_ep"], 6.0)
            self.assertEqual(updated["last_known_episode"], 5.0)

            # 4. Delete
            delete_ongoing_watchlist("Psyren", conn=conn)
            self.assertEqual(len(get_ongoing_watchlist(active_only=False, conn=conn)), 0)

    def test_episode_notifications_and_duplicate_prevention(self):
        with db_connection(self.db_path) as conn:
            # 1. Save new notification
            n1 = save_episode_notification(
                anime_name="Doumo Suki",
                episode_number="7",
                episode_title="Doumo Suki Episode 7",
                episode_url="https://example.com/ep7",
                thumbnail="https://example.com/thumb.jpg",
                released_on="2 Jam yang lalu",
                conn=conn
            )
            self.assertIsNotNone(n1)
            self.assertEqual(get_unread_notification_count(conn=conn), 1)

            # 2. Duplicate insertion should return None and not increment count
            n2 = save_episode_notification(
                anime_name="Doumo Suki",
                episode_number="7",
                episode_title="Doumo Suki Episode 7 Duplicate",
                conn=conn
            )
            self.assertIsNone(n2)
            self.assertEqual(get_unread_notification_count(conn=conn), 1)

            # 3. Retrieve notifications
            notifs = get_episode_notifications(unread_only=True, conn=conn)
            self.assertEqual(len(notifs), 1)
            self.assertEqual(notifs[0]["anime_name"], "Doumo Suki")
            self.assertEqual(notifs[0]["episode_number"], "7")
            self.assertFalse(notifs[0]["is_read"])
            self.assertFalse(notifs[0]["is_downloaded"])

            # 4. Mark single read
            mark_notification_read(notification_id=n1, conn=conn)
            self.assertEqual(get_unread_notification_count(conn=conn), 0)

            # 5. Insert another, then mark downloaded
            n3 = save_episode_notification(
                anime_name="Doumo Suki",
                episode_number="8",
                episode_title="Doumo Suki Episode 8",
                conn=conn
            )
            self.assertIsNotNone(n3)
            self.assertEqual(get_unread_notification_count(conn=conn), 1)

            affected = mark_notification_downloaded("Doumo Suki", "8", conn=conn)
            self.assertEqual(affected, 1)
            self.assertEqual(get_unread_notification_count(conn=conn), 0)


class TestWatchlistTrackerLogic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_logic.db")
        init_db(self.db_path)
        self.db_patcher = patch("anibase.constants.DB_PATH", self.db_path)
        self.db_patcher.start()

    def tearDown(self):
        self.db_patcher.stop()
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_extract_episode_number(self):
        self.assertEqual(extract_episode_number("Episode 7"), 7.0)
        self.assertEqual(extract_episode_number("08"), 8.0)
        self.assertEqual(extract_episode_number("Doumo Suki - 12.5 [1080p]"), 12.5)
        self.assertEqual(extract_episode_number("E9"), 9.0)
        self.assertEqual(extract_episode_number(10), 10.0)
        self.assertIsNone(extract_episode_number("No Numbers Here"))
        self.assertIsNone(extract_episode_number(None))

    @patch("anibase.watchlist_tracker.get_anime_local_episodes")
    def test_scan_ongoing_anime(self, mock_local_eps):
        mock_local_eps.return_value = {"episodes": [1, 2, 3], "folder_name": "Test Anime", "matched": True}
        with tempfile.TemporaryDirectory() as tmp_dir:
            ongoing_folder = os.path.join(tmp_dir, "Onggoing")
            frieren_dir = os.path.join(ongoing_folder, "Sousou no Frieren")
            os.makedirs(frieren_dir)
            with open(os.path.join(frieren_dir, "Frieren_01.mkv"), "w") as f:
                f.write("dummy")

            settings = {
                "ongoing_path": ongoing_folder,
                "library_paths": [ongoing_folder]
            }

            scanned = scan_ongoing_anime(settings)
            self.assertTrue(any(item["anime_name"] == "Sousou no Frieren" for item in scanned))

    @patch("anibase.watchlist_tracker.get_samehadaku_latest_episodes")
    @patch("anibase.watchlist_tracker.get_anime_local_episodes")
    def test_check_ongoing_releases_detects_new_episode(self, mock_local_eps, mock_latest):
        mock_local_eps.return_value = {"episodes": [1, 2, 3, 4, 5, 6], "folder_name": "Doumo Suki", "matched": True}
        mock_latest.return_value = [
            {
                "title": "Doumo Suki na Hito ni Horegusuri wo Irai sareta Majo desu.",
                "episode": "Episode 7",
                "episode_number": "7",
                "episode_url": "https://v2.samehadaku.how/doumo-suki-episode-7/",
                "anime_url": "https://v2.samehadaku.how/anime/doumo-suki/",
                "thumbnail": "https://example.com/thumb.jpg",
                "released_on": "2 Jam yang lalu"
            }
        ]

        # Pastikan anime ada di watchlist
        upsert_ongoing_watchlist("Doumo Suki", is_tracked=1, source="ongoing_folder")

        res = check_ongoing_releases(deep=False)
        self.assertIn("new_alerts", res)
        self.assertIn("checked_count", res)

    @patch("anibase.watchlist_tracker.mark_notification_downloaded")
    def test_on_download_completed(self, mock_mark):
        on_download_completed("Doumo Suki", "D:\\Anime\\Doumo Suki - Ep 7 [1080p].mkv")
        mock_mark.assert_called_with("Doumo Suki", 7.0)


class TestWatchlistTrackerAPI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_api.db")
        self.settings_path = os.path.join(self.tmp_dir.name, "settings.json")
        init_db(self.db_path)
        self.db_patcher = patch("anibase.constants.DB_PATH", self.db_path)
        self.db_patcher.start()
        self.settings_patcher = patch("anibase.constants.SETTINGS_FILE", self.settings_path)
        self.settings_patcher.start()
        self.setup_patcher = patch("anibase.web.helpers.is_setup_complete", return_value=True)
        self.setup_patcher.start()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.token = get_action_token()

    def tearDown(self):
        self.setup_patcher.stop()
        self.settings_patcher.stop()
        self.db_patcher.stop()
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_api_ongoing_tracker_list(self):
        res = self.client.get("/api/ongoing-tracker/list")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("tracked_anime", data)
        self.assertIn("unread_notifications_count", data)

    @patch("anibase.watchlist_tracker.check_ongoing_releases")
    def test_api_ongoing_tracker_check(self, mock_check):
        mock_check.return_value = {
            "checked_count": 2,
            "new_alerts_count": 1,
            "new_alerts": [{"anime_name": "Test Anime", "episode_number": "5"}],
            "unread_notifications_count": 1,
            "tracked_anime": []
        }
        res = self.client.post(
            "/api/ongoing-tracker/check",
            headers={"X-Action-Token": self.token, "Content-Type": "application/json"},
            json={"deep": False}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["new_alerts_count"], 1)

    def test_api_ongoing_tracker_toggle_and_delete(self):
        res = self.client.post(
            "/api/ongoing-tracker/toggle",
            headers={"X-Action-Token": self.token, "Content-Type": "application/json"},
            json={"anime_name": "Dandadan", "is_tracked": True}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["anime_name"], "Dandadan")
        self.assertTrue(data["is_tracked"])

        # Test delete endpoint
        del_res = self.client.post(
            "/api/ongoing-tracker/delete",
            headers={"X-Action-Token": self.token, "Content-Type": "application/json"},
            json={"anime_name": "Dandadan"}
        )
        self.assertEqual(del_res.status_code, 200)
        del_data = del_res.get_json()
        self.assertTrue(del_data["ok"])
        self.assertTrue(del_data["deleted"])

    def test_api_ongoing_tracker_notifications_and_read(self):
        # Insert test notification directly
        save_episode_notification(
            anime_name="API Test Anime",
            episode_number="12",
            episode_title="Episode 12",
        )

        res = self.client.get("/api/ongoing-tracker/notifications?unread_only=true")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["notifications"], list)

        # Test invalid/non-numeric limit safely falls back to bounded default instead of 500
        res_bad_limit = self.client.get("/api/ongoing-tracker/notifications?limit=abc")
        self.assertEqual(res_bad_limit.status_code, 200)
        self.assertTrue(res_bad_limit.get_json()["ok"])

        # Mark all read
        res_read = self.client.post(
            "/api/ongoing-tracker/notifications/read",
            headers={"X-Action-Token": self.token, "Content-Type": "application/json"},
            json={"all": True}
        )
        self.assertEqual(res_read.status_code, 200)
        data_read = res_read.get_json()
        self.assertTrue(data_read["ok"])
        self.assertEqual(data_read["unread_count"], 0)

    def test_api_ongoing_tracker_read_routes_enforce_host_only(self):
        with patch("anibase.web.helpers.load_settings", return_value={"lan_access_enabled": True, "setup_completed": True}):
            for endpoint in ("/api/ongoing-tracker/list", "/api/ongoing-tracker/notifications"):
                res = self.client.get(endpoint, environ_base={"REMOTE_ADDR": "192.168.1.50"})
                self.assertEqual(res.status_code, 403, endpoint)
                self.assertEqual(res.get_json()["error"], "host_only")

    @patch("anibase.watchlist_tracker.quick_download_episode_alert")
    def test_api_ongoing_tracker_quick_download(self, mock_quick):
        mock_quick.return_value = {"ok": True, "task": {"task_id": "dl_quick123"}}
        res = self.client.post(
            "/api/ongoing-tracker/quick-download",
            headers={"X-Action-Token": self.token, "Content-Type": "application/json"},
            json={
                "anime_name": "Doumo Suki",
                "episode_url": "https://example.com/ep7",
                "quality": "1080p",
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["task"]["task_id"], "dl_quick123")


    @patch("anibase.watchlist_tracker.start_download")
    @patch("anibase.watchlist_tracker.resolve_best_direct_link")
    def test_quick_download_episode_alert(self, mock_resolve, mock_start):
        mock_resolve.return_value = {
            "direct_url": "https://pixeldrain.com/api/file/abc?download",
            "quality": "1080p",
            "format": "MKV",
            "host": "Pixeldrain",
            "provider": "samehadaku"
        }
        mock_start.return_value = {"ok": True, "task": {"task_id": "dl_test_123"}}

        res = quick_download_episode_alert(
            anime_name="Doumo Suki",
            episode_url="https://v2.samehadaku.how/doumo-suki-episode-7/",
            preferred_quality="1080p",
            preferred_format="MKV"
        )
        self.assertTrue(res["ok"])
        mock_start.assert_called_once_with(
            url="https://pixeldrain.com/api/file/abc?download",
            anime_name="Doumo Suki",
            episode="7.0",
            filename="Doumo Suki - 1080p.mkv",
            provider="samehadaku"
        )

    @patch("anibase.download_manager._download_worker")
    def test_start_download_keyword_argument_compatibility(self, _mock_worker):
        from anibase.download_manager import start_download
        # Calling start_download with legacy/alternate kwargs should not throw TypeError
        res = start_download(
            download_url="https://example.com/safelink-test",
            episode_label="Ep 01",
            anime_name="Test Anime"
        )
        self.assertIsInstance(res, dict)
        self.assertIn("ok", res)


if __name__ == "__main__":
    unittest.main()
