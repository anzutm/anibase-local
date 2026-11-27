import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from anibase.constants import APP_VERSION
from anibase.desktop import updater


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmp_dir.name, "version_state.json")
        self.user_data_patch = patch.object(updater, "get_user_data_dir", return_value=self.tmp_dir.name)
        self.user_data_patch.start()

    def tearDown(self):
        self.user_data_patch.stop()
        self.tmp_dir.cleanup()

    def test_parse_version_tuple(self):
        self.assertEqual(updater.parse_version_tuple("v1.5.5"), (1, 5, 5))
        self.assertEqual(updater.parse_version_tuple("1.5.6"), (1, 5, 6))
        self.assertEqual(updater.parse_version_tuple("2.0"), (2, 0, 0))
        self.assertEqual(updater.parse_version_tuple("v1.5.5-alpha.1"), (1, 5, 5))
        self.assertEqual(updater.parse_version_tuple(""), (0, 0, 0))
        self.assertEqual(updater.parse_version_tuple(None), (0, 0, 0))

    def test_is_version_newer(self):
        self.assertTrue(updater.is_version_newer("1.5.6", "1.5.5"))
        self.assertTrue(updater.is_version_newer("v2.0.0", "1.5.5"))
        self.assertFalse(updater.is_version_newer("1.5.5", "1.5.5"))
        self.assertFalse(updater.is_version_newer("1.5.4", "1.5.5"))
        self.assertFalse(updater.is_version_newer("1.0.9", "1.5.5"))

    def test_load_and_save_version_state(self):
        state = updater.load_version_state()
        self.assertEqual(state, {})

        test_data = {"last_run_version": "1.5.5", "test_key": "val"}
        saved = updater.save_version_state(test_data)
        self.assertTrue(saved)
        self.assertTrue(os.path.isfile(self.state_file))

        loaded = updater.load_version_state()
        self.assertEqual(loaded.get("last_run_version"), "1.5.5")
        self.assertEqual(loaded.get("test_key"), "val")

    @patch.object(updater, "send_desktop_notification")
    def test_check_post_update_notification_first_run(self, mock_notify):
        with patch("os.name", "posix"):
            res = updater.check_post_update_notification(notify_on_upgrade=True)
            self.assertFalse(res["is_upgrade"])
            self.assertEqual(res["current_version"], APP_VERSION)
            mock_notify.assert_called_once()
            state = updater.load_version_state()
            self.assertEqual(state["last_run_version"], APP_VERSION)

    @patch.object(updater, "send_desktop_notification")
    def test_check_post_update_notification_same_version(self, mock_notify):
        updater.save_version_state({"last_run_version": APP_VERSION})
        res = updater.check_post_update_notification(notify_on_upgrade=True)
        self.assertFalse(res["is_upgrade"])
        mock_notify.assert_not_called()

    @patch.object(updater, "send_desktop_notification")
    def test_check_post_update_notification_upgraded(self, mock_notify):
        updater.save_version_state({"last_run_version": "1.5.4"})
        res = updater.check_post_update_notification(notify_on_upgrade=True)
        self.assertTrue(res["is_upgrade"])
        self.assertEqual(res["previous_version"], "1.5.4")
        self.assertEqual(res["current_version"], APP_VERSION)
        mock_notify.assert_called_once()
        state = updater.load_version_state()
        self.assertEqual(state["last_run_version"], APP_VERSION)

    @patch("urllib.request.urlopen")
    @patch.object(updater, "send_desktop_notification")
    def test_check_github_release_new_update(self, mock_notify, mock_urlopen):
        payload = json.dumps({
            "tag_name": "v1.6.0",
            "name": "AniBase v1.6.0",
            "html_url": "https://github.com/anzutm/anibase-local/releases/tag/v1.6.0",
            "body": "New features and fixes"
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = payload
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = updater.check_github_release(force=True, notify=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest_version"], "1.6.0")
        mock_notify.assert_called_once()

        # Re-checking should throttle notification because last_notified_release is stored
        mock_notify.reset_mock()
        res2 = updater.check_github_release(force=True, notify=True)
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["update_available"])
        mock_notify.assert_not_called()

    @patch("urllib.request.urlopen")
    @patch.object(updater, "send_desktop_notification")
    def test_check_github_release_no_update(self, mock_notify, mock_urlopen):
        payload = json.dumps({
            "tag_name": "v1.5.5",
            "name": "AniBase v1.5.5",
            "html_url": "https://github.com/anzutm/anibase-local/releases/tag/v1.5.5",
            "body": "Current release"
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = payload
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = updater.check_github_release(force=True, notify=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["update_available"])
        mock_notify.assert_not_called()

    @patch("urllib.request.urlopen", side_effect=OSError("Network unreachable"))
    def test_check_github_release_network_error(self, mock_urlopen):
        res = updater.check_github_release(force=True, notify=True)
        self.assertFalse(res["ok"])
        self.assertIn("Network unreachable", res.get("error", ""))
        self.assertFalse(res["update_available"])

    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_send_desktop_notification_notify_send(self, mock_subproc, mock_which):
        with patch("os.name", "posix"):
            res = updater.send_desktop_notification("Test Title", "Test Message")
            self.assertTrue(res)

    def test_send_desktop_notification_windows_uses_registered_tray_handler(self):
        received = []

        def fake_handler(title, message, expire_ms):
            received.append((title, message, expire_ms))
            return True

        try:
            updater.register_desktop_notification_handler(fake_handler)
            with patch("os.name", "nt"):
                res = updater.send_desktop_notification("Win Update", "New release v1.6.0", expire_time_ms=5000)
            self.assertTrue(res)
            self.assertEqual(received, [("Win Update", "New release v1.6.0", 5000)])
        finally:
            updater.register_desktop_notification_handler(None)


if __name__ == "__main__":
    unittest.main()
