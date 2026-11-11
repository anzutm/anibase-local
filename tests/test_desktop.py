import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from anibase.desktop.shell import (
    DesktopApi,
    load_saved_window_state,
    save_current_window_state,
    restore_desktop_window,
    apply_windows_11_attributes,
    on_window_closing,
    on_window_maximized,
    on_window_restored,
    on_window_fullscreened,
    on_window_resized,
    on_window_moved,
    _window_state,
)
import anibase.desktop.shell as desktop_shell
import anibase.constants as anibase_constants
from anibase.settings import load_settings, save_settings, get_action_token, get_default_settings, apply_settings
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

    def test_toggle_fullscreen(self):
        with patch("anibase.desktop.shell.desktop_window", self.mock_window):
            desktop_shell._window_state["fullscreen"] = False
            self.mock_window.native.is_fullscreen = False
            self.mock_window.native.InvokeRequired = False
            self.api.toggle_fullscreen()
            self.assertTrue(desktop_shell._window_state["fullscreen"])
            self.mock_window.native.toggle_fullscreen.assert_called_once()

    def test_set_fullscreen(self):
        with patch("anibase.desktop.shell.desktop_window", self.mock_window):
            desktop_shell._window_state["fullscreen"] = False
            self.mock_window.native.is_fullscreen = False
            self.mock_window.native.InvokeRequired = False
            self.api.set_fullscreen(True)
            self.assertTrue(desktop_shell._window_state["fullscreen"])
            self.api.set_fullscreen(False)
            self.assertFalse(desktop_shell._window_state["fullscreen"])

    def test_is_fullscreen(self):
        desktop_shell._window_state["fullscreen"] = True
        self.assertTrue(self.api.is_fullscreen())
        desktop_shell._window_state["fullscreen"] = False
        self.assertFalse(self.api.is_fullscreen())

    def test_close_hides_window_when_minimize_to_tray_true(self):
        with patch("anibase.desktop.shell.save_current_window_state") as mock_save, \
             patch("anibase.desktop.shell.load_settings", return_value={"desktop_minimize_to_tray": True}):
            self.api.close()
            mock_save.assert_called_once()
            self.mock_window.hide.assert_called_once()

    def test_close_destroys_window_when_minimize_to_tray_false(self):
        with patch("anibase.desktop.shell.save_current_window_state") as mock_save, \
             patch("anibase.desktop.shell.load_settings", return_value={"desktop_minimize_to_tray": False}), \
             patch("anibase.desktop.shell.close_desktop_window_permanent") as mock_close_perm:
            self.api.close()
            mock_save.assert_called_once()
            mock_close_perm.assert_called_once()

    @patch("webbrowser.open")
    def test_open_external_calls_webbrowser(self, mock_webbrowser):
        self.api.open_external("https://myanimelist.net")
        mock_webbrowser.assert_called_once_with("https://myanimelist.net")

    def test_get_state_returns_dict(self):
        desktop_shell._window_state["maximized"] = False
        desktop_shell._window_state["fullscreen"] = False
        desktop_shell._window_state["width"] = 1400
        desktop_shell._window_state["height"] = 900
        desktop_shell._window_state["zoom"] = 1.25
        state = self.api.get_state()
        self.assertEqual(state, {
            "maximized": False,
            "fullscreen": False,
            "width": 1400,
            "height": 900,
            "zoom": 1.25,
        })

    def test_api_zoom_controls(self):
        self.api.reset_zoom()
        self.assertAlmostEqual(self.api.get_zoom(), 1.0)

        # Zoom in step
        res = self.api.zoom_in()
        self.assertAlmostEqual(res["factor"], 1.10)
        self.assertAlmostEqual(self.api.get_zoom(), 1.10)

        # Next zoom in step
        res = self.api.zoom_in()
        self.assertAlmostEqual(res["factor"], 1.25)

        # Zoom out step
        res = self.api.zoom_out()
        self.assertAlmostEqual(res["factor"], 1.10)

        # Reset zoom
        res = self.api.reset_zoom()
        self.assertAlmostEqual(res["factor"], 1.0)
        self.assertAlmostEqual(self.api.get_zoom(), 1.0)

    def test_api_set_zoom_clamps(self):
        # Exceeds max (3.0)
        res = self.api.set_zoom(5.0)
        self.assertAlmostEqual(res["factor"], 3.0)

        # Below min (0.5)
        res = self.api.set_zoom(0.1)
        self.assertAlmostEqual(res["factor"], 0.5)

        # Reset back
        self.api.reset_zoom()

    def test_configure_webview2_settings(self):
        mock_win = MagicMock()
        mock_core = MagicMock()
        mock_settings = MagicMock()
        mock_core.Settings = mock_settings
        mock_webview_ctrl = MagicMock()
        mock_webview_ctrl.CoreWebView2 = mock_core
        mock_win.native.webview = mock_webview_ctrl
        mock_win.native.InvokeRequired = False

        desktop_shell._window_state["zoom"] = 1.25
        res = desktop_shell.configure_webview2_settings(mock_win)
        self.assertTrue(res)
        self.assertTrue(mock_settings.AreBrowserAcceleratorKeysEnabled)
        self.assertTrue(mock_settings.IsZoomControlEnabled)
        self.assertEqual(mock_core.ZoomFactor, 1.25)
        desktop_shell._window_state["zoom"] = 1.0

    @patch("anibase.desktop.shell.get_window_hwnd", return_value=12345)
    def test_set_desktop_fullscreen_win32_thickframe(self, mock_hwnd):
        mock_win = MagicMock()
        mock_win.native.InvokeRequired = False
        mock_win.native.is_fullscreen = False

        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("os.name", "nt"), \
             patch("ctypes.windll") as mock_windll, \
             patch("anibase.desktop.shell.apply_windows_11_attributes") as mock_apply_w11:
            mock_user32 = MagicMock()
            # Current style has WS_THICKFRAME (0x00040000)
            mock_user32.GetWindowLongW.return_value = 0x10040000
            mock_windll.user32 = mock_user32

            desktop_shell._window_state["fullscreen"] = False
            desktop_shell.set_desktop_fullscreen(True)
            self.assertTrue(desktop_shell._window_state["fullscreen"])
            # WS_THICKFRAME cleared
            mock_user32.SetWindowLongW.assert_called_once_with(12345, -16, 0x10000000)

            # Exiting fullscreen
            mock_win.native.is_fullscreen = True
            desktop_shell.set_desktop_fullscreen(False)
            self.assertFalse(desktop_shell._window_state["fullscreen"])
            mock_apply_w11.assert_called_once_with(12345)


class WindowStateTests(unittest.TestCase):
    def test_load_saved_window_state_clamps_to_screen(self):
        mock_settings = {
            "desktop_window": {
                "x": 100,
                "y": 150,
                "width": 1400,
                "height": 900,
                "maximized": True,
                "zoom": 1.25,
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
            self.assertEqual(desktop_shell._window_state["zoom"], 1.25)

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
            desktop_shell._window_state["fullscreen"] = False
            desktop_shell._window_state["zoom"] = 1.10
            save_current_window_state()
            self.assertIn("desktop_window", saved_dict)
            self.assertEqual(saved_dict["desktop_window"]["width"], 1350)
            self.assertEqual(saved_dict["desktop_window"]["height"], 850)
            self.assertEqual(saved_dict["desktop_window"]["x"], 200)
            self.assertEqual(saved_dict["desktop_window"]["y"], 100)
            self.assertEqual(saved_dict["desktop_window"]["zoom"], 1.10)

    def test_on_window_closing_intercepts_when_tray_enabled(self):
        mock_win = MagicMock()
        desktop_shell._is_shutting_down = False
        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell.load_settings", return_value={"desktop_minimize_to_tray": True}), \
             patch("anibase.desktop.shell.save_current_window_state"):
            ret = on_window_closing()
            self.assertFalse(ret)
            mock_win.hide.assert_called_once()

    def test_on_window_closing_allows_close_when_tray_disabled(self):
        desktop_shell._is_shutting_down = False
        with patch("anibase.desktop.shell.load_settings", return_value={"desktop_minimize_to_tray": False}), \
             patch("anibase.desktop.shell.save_current_window_state"):
            ret = on_window_closing()
            self.assertTrue(ret)
            self.assertTrue(desktop_shell._is_shutting_down)
        desktop_shell._is_shutting_down = False

    def test_on_window_fullscreened(self):
        mock_win = MagicMock()
        with patch("anibase.desktop.shell.desktop_window", mock_win):
            on_window_fullscreened()
            self.assertTrue(desktop_shell._window_state["fullscreen"])
            mock_win.evaluate_js.assert_called_once()

    def test_on_window_restored(self):
        mock_win = MagicMock()
        with patch("anibase.desktop.shell.desktop_window", mock_win):
            on_window_restored()
            self.assertFalse(desktop_shell._window_state["maximized"])
            self.assertFalse(desktop_shell._window_state["fullscreen"])
            mock_win.evaluate_js.assert_called_once()


class Windows11DWMTests(unittest.TestCase):
    def test_apply_windows_11_attributes(self):
        with patch("os.name", "nt"), \
             patch("ctypes.windll") as mock_windll:
            mock_dwmapi = MagicMock()
            mock_dwmapi.DwmSetWindowAttribute.return_value = 0
            mock_user32 = MagicMock()
            mock_user32.GetWindowLongW.return_value = 0x10000000
            mock_windll.dwmapi = mock_dwmapi
            mock_windll.user32 = mock_user32

            apply_windows_11_attributes(12345)

            # Assert DWM calls for dark mode (20), corner preference (33), border color (34)
            calls = [c[0][1] for c in mock_dwmapi.DwmSetWindowAttribute.call_args_list]
            self.assertIn(20, calls)
            self.assertIn(33, calls)
            self.assertIn(34, calls)
            # Assert Win32 SetWindowLongW was called to add WS_THICKFRAME
            mock_user32.SetWindowLongW.assert_called_once()


class DesktopWebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_settings_file = anibase_constants.SETTINGS_FILE
        self.orig_anime_paths = anibase_constants.ANIME_PATHS
        self.test_settings_file = os.path.join(self.temp_dir.name, "settings.json")
        anibase_constants.SETTINGS_FILE = self.test_settings_file
        test_defaults = get_default_settings()
        test_defaults["setup_completed"] = True
        save_settings(test_defaults)
        apply_settings(test_defaults)

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        anibase_constants.SETTINGS_FILE = self.orig_settings_file
        anibase_constants.ANIME_PATHS = self.orig_anime_paths
        self.temp_dir.cleanup()

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
        self.assertIn('id="desktopTitlebarTitle"', html)
        self.assertIn('id="desktopSplash"', html)

    def test_anime_detail_includes_desktop_titlebar(self):
        lib_dir = os.path.join(self.temp_dir.name, "Anime")
        anime_dir = os.path.join(lib_dir, "TestAnime")
        os.makedirs(anime_dir, exist_ok=True)
        season_dir = os.path.join(anime_dir, "Season 1")
        os.makedirs(season_dir, exist_ok=True)
        open(os.path.join(season_dir, "Episode 1.mp4"), "w").close()

        saved = get_default_settings()
        saved["library_paths"] = [lib_dir]
        saved["watchlist_path"] = lib_dir
        saved["setup_completed"] = True
        save_settings(saved)
        apply_settings(saved)

        resp = self.client.get("/anime/TestAnime")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('class="desktop-titlebar pywebview-drag-region"', html)
        self.assertIn("desktop-titlebar.css", html)
        self.assertIn("desktop-titlebar.js", html)

    def test_missing_anime_renders_styled_404(self):
        resp = self.client.get("/anime/NonExistentAnime12345")
        self.assertEqual(resp.status_code, 404)
        html = resp.get_data(as_text=True)
        self.assertIn("Anime Folder Not Found", html)
        self.assertIn("NonExistentAnime12345", html)
        self.assertIn("desktop-titlebar.css", html)

    def test_settings_page_renders_desktop_options(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="settings-desktop"', html)
        self.assertIn('name="desktop_minimize_to_tray"', html)
        self.assertIn('name="desktop_start_minimized"', html)

    def test_settings_post_updates_desktop_preferences(self):
        with self.app.test_request_context():
            token = get_action_token()

        initial_settings = load_settings()
        initial_settings["desktop_window"] = {"width": 1400, "height": 900}
        save_settings(initial_settings)

        resp = self.client.post("/settings", data={
            "action_token": token,
            "desktop_settings_present": "1",
            "desktop_minimize_to_tray": "on",
            # desktop_start_minimized left out (unchecked)
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        updated = load_settings()
        self.assertTrue(updated["desktop_minimize_to_tray"])
        self.assertFalse(updated["desktop_start_minimized"])
        # Preserved desktop_window
        self.assertEqual(updated.get("desktop_window", {}).get("width"), 1400)

    def test_build_assets_include_desktop_titlebar(self):
        self.assertIn("templates/_desktop_titlebar.html", build.REQUIRED_ASSETS)
        self.assertIn("templates/anime_not_found.html", build.REQUIRED_ASSETS)
        self.assertIn("static/desktop-titlebar.css", build.REQUIRED_ASSETS)
        self.assertIn("static/desktop-titlebar.js", build.REQUIRED_ASSETS)


if __name__ == "__main__":
    unittest.main()
