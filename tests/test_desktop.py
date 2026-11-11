import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from anibase.desktop.shell import (
    DesktopApi,
    load_saved_window_state,
    save_current_window_state,
    restore_desktop_window,
    on_window_closing,
    on_window_maximized,
    on_window_restored,
    on_window_resized,
    on_window_moved,
    _window_state,
)
import anibase.desktop.shell as desktop_shell
from anibase.web import create_app
import build


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.mock_window = MagicMock()
        self.api = DesktopApi(self.mock_window)

    def test_minimize_delegates_to_window(self):
        self.api.minimize()
        self.mock_window.minimize.assert_called_once()

    def test_toggle_maximize_restores_when_maximized(self):
        desktop_shell._window_state["maximized"] = True
        self.api.toggle_maximize()
        self.mock_window.restore.assert_called_once()

    def test_toggle_maximize_maximizes_when_not_maximized(self):
        desktop_shell._window_state["maximized"] = False
        self.api.toggle_maximize()
        self.mock_window.maximize.assert_called_once()

    def test_close_hides_window(self):
        with patch("anibase.desktop.shell.save_current_window_state") as mock_save:
            self.api.close()
            mock_save.assert_called_once()
            self.mock_window.hide.assert_called_once()

    @patch("webbrowser.open")
    def test_open_external_calls_webbrowser(self, mock_webbrowser):
        self.api.open_external("https://myanimelist.net")
        mock_webbrowser.assert_called_once_with("https://myanimelist.net")

    def test_get_state_returns_dict(self):
        desktop_shell._window_state["maximized"] = False
        desktop_shell._window_state["width"] = 1400
        desktop_shell._window_state["height"] = 900
        state = self.api.get_state()
        self.assertEqual(state, {"maximized": False, "width": 1400, "height": 900})


class WindowStateTests(unittest.TestCase):
    def test_load_saved_window_state_clamps_to_screen(self):
        mock_settings = {
            "desktop_window": {
                "x": 100,
                "y": 150,
                "width": 1400,
                "height": 900,
                "maximized": True,
            }
        }
        with patch("anibase.desktop.shell.load_settings", return_value=mock_settings), \
             patch("anibase.desktop.shell.get_screen_bounds", return_value=(1920, 1080)):
            load_saved_window_state()
            self.assertEqual(desktop_shell._window_state["width"], 1400)
            self.assertEqual(desktop_shell._window_state["height"], 900)
            self.assertEqual(desktop_shell._window_state["x"], 100)
            self.assertEqual(desktop_shell._window_state["y"], 150)
            self.assertTrue(desktop_shell._window_state["maximized"])

    def test_save_current_window_state(self):
        saved_dict = {}
        def fake_save(s):
            saved_dict.update(s)

        mock_window = MagicMock()
        mock_window.width = 1350
        mock_window.height = 850
        mock_window.x = 200
        mock_window.y = 100

        with patch("anibase.desktop.shell.desktop_window", mock_window), \
             patch("anibase.desktop.shell.load_settings", return_value={}), \
             patch("anibase.desktop.shell.save_settings", side_effect=fake_save):
            desktop_shell._window_state["maximized"] = False
            save_current_window_state()
            self.assertIn("desktop_window", saved_dict)
            self.assertEqual(saved_dict["desktop_window"]["width"], 1350)
            self.assertEqual(saved_dict["desktop_window"]["height"], 850)
            self.assertEqual(saved_dict["desktop_window"]["x"], 200)
            self.assertEqual(saved_dict["desktop_window"]["y"], 100)

    def test_on_window_closing_intercepts_when_not_shutting_down(self):
        mock_win = MagicMock()
        desktop_shell._is_shutting_down = False
        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell.save_current_window_state"):
            ret = on_window_closing()
            self.assertFalse(ret)
            mock_win.hide.assert_called_once()

    def test_on_window_closing_allows_close_when_shutting_down(self):
        desktop_shell._is_shutting_down = True
        ret = on_window_closing()
        self.assertTrue(ret)
        desktop_shell._is_shutting_down = False


class DesktopWebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_api_desktop_activate_success(self):
        with patch("anibase.desktop.shell.restore_desktop_window", return_value=True):
            resp = self.client.post("/api/desktop/activate")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(data.get("restored"))

    def test_api_desktop_activate_fallback(self):
        with patch("anibase.desktop.shell.restore_desktop_window", return_value=False):
            resp = self.client.post("/api/desktop/activate")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertFalse(data.get("restored"))

    def test_titlebar_rendered_in_base_template(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('class="desktop-titlebar pywebview-drag-region"', html)
        self.assertIn("desktop-titlebar.css", html)
        self.assertIn("desktop-titlebar.js", html)
        self.assertIn('id="desktopBtnMin"', html)
        self.assertIn('id="desktopBtnMax"', html)
        self.assertIn('id="desktopBtnClose"', html)

    def test_build_assets_include_desktop_titlebar(self):
        self.assertIn("templates/_desktop_titlebar.html", build.REQUIRED_ASSETS)
        self.assertIn("static/desktop-titlebar.css", build.REQUIRED_ASSETS)
        self.assertIn("static/desktop-titlebar.js", build.REQUIRED_ASSETS)


if __name__ == "__main__":
    unittest.main()
