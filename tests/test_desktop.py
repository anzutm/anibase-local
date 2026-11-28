import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

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
        # toggle_fullscreen now offloads to a background thread to avoid UI-thread deadlock.
        # We patch toggle_desktop_fullscreen and verify it is called (via thread).
        with patch("anibase.desktop.shell.toggle_desktop_fullscreen") as mock_toggle:
            self.api.toggle_fullscreen()
            import time; time.sleep(0.1)  # allow background thread to run
            mock_toggle.assert_called_once()

    def test_set_fullscreen(self):
        # set_fullscreen now offloads to a background thread to avoid UI-thread deadlock.
        # We patch desktop_window + set_desktop_fullscreen and verify state changes via thread.
        with patch("anibase.desktop.shell.desktop_window", self.mock_window):
            desktop_shell._window_state["fullscreen"] = False
            self.mock_window.native.is_fullscreen = False
            # Patch set_desktop_fullscreen to bypass real window/JS calls in unit test
            with patch("anibase.desktop.shell.set_desktop_fullscreen", wraps=lambda enable: (
                desktop_shell._window_state.update({"fullscreen": bool(enable)}) or bool(enable)
            )) as mock_set:
                self.api.set_fullscreen(True)
                import time; time.sleep(0.1)  # allow background thread to run
                mock_set.assert_called_once_with(True)
                self.assertTrue(desktop_shell._window_state["fullscreen"])

                desktop_shell._window_state["fullscreen"] = True
                mock_set.reset_mock()
                self.api.set_fullscreen(False)
                time.sleep(0.1)
                mock_set.assert_called_once_with(False)
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
        with patch("os.name", "nt"):
            res = desktop_shell.configure_webview2_settings(mock_win)
        self.assertTrue(res)
        self.assertTrue(mock_settings.AreBrowserAcceleratorKeysEnabled)
        self.assertFalse(mock_settings.IsZoomControlEnabled)
        desktop_shell._window_state["zoom"] = 1.0

    @patch("anibase.desktop.shell.get_window_hwnd", return_value=12345)
    def test_set_desktop_fullscreen_win32_thickframe(self, mock_hwnd):
        mock_win = MagicMock()
        mock_win.native.InvokeRequired = False
        mock_win.native.is_fullscreen = False

        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("os.name", "nt"), \
             patch("ctypes.windll", create=True) as mock_windll, \
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

    def test_on_window_closing_allows_close_when_tray_unavailable(self):
        desktop_shell._is_shutting_down = False
        with patch("anibase.desktop.shell.load_settings", return_value={"desktop_minimize_to_tray": True}), \
             patch("anibase.desktop.shell.is_system_tray_available", return_value=False), \
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
             patch("ctypes.windll", create=True) as mock_windll:
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

        self.req_patcher = patch("requests.get", side_effect=requests.RequestException("No network in tests"))
        self.req_patcher.start()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.req_patcher.stop()
        anibase_constants.SETTINGS_FILE = self.orig_settings_file
        anibase_constants.ANIME_PATHS = self.orig_anime_paths
        self.temp_dir.cleanup()

    def test_api_desktop_activate_success(self):
        token = get_action_token()
        with patch("anibase.desktop.shell.restore_desktop_window", return_value=True):
            resp = self.client.post("/api/desktop/activate", headers={"X-Action-Token": token})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(data.get("restored"))

    def test_api_desktop_activate_fallback(self):
        token = get_action_token()
        with patch("anibase.desktop.shell.restore_desktop_window", return_value=False):
            resp = self.client.post("/api/desktop/activate", headers={"X-Action-Token": token})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertFalse(data.get("restored"))

    def test_desktop_post_endpoints_require_action_token(self):
        for endpoint in ("/api/desktop/activate", "/api/desktop/reset-window", "/api/desktop/shutdown"):
            resp = self.client.post(endpoint)
            self.assertEqual(resp.status_code, 403, f"Expected 403 without token for {endpoint}")
            self.assertEqual(resp.get_json().get("error"), "invalid_action_token")

    def test_desktop_api_endpoints_allowed_when_setup_not_completed(self):
        """Desktop API endpoints must not be redirected to /setup when setup_completed is False."""
        uncompleted_settings = get_default_settings()
        uncompleted_settings["setup_completed"] = False
        uncompleted_settings["library_paths"] = []
        save_settings(uncompleted_settings)
        apply_settings(uncompleted_settings)
        token = get_action_token()

        # Normal page is redirected to /setup
        res_index = self.client.get("/")
        self.assertEqual(res_index.status_code, 302)
        self.assertIn("/setup", res_index.headers.get("Location", ""))

        # Desktop activate endpoint returns 200 JSON (not 302 redirect)
        with patch("anibase.desktop.shell.restore_desktop_window", return_value=True):
            res_activate = self.client.post("/api/desktop/activate", headers={"X-Action-Token": token})
            self.assertEqual(res_activate.status_code, 200)
            self.assertTrue(res_activate.get_json().get("ok"))

        # Desktop status endpoint returns 200 JSON
        with patch("anibase.desktop.shell.is_system_startup_enabled", return_value=False):
            res_status = self.client.get("/api/desktop/status")
            self.assertEqual(res_status.status_code, 200)
            self.assertTrue(res_status.get_json().get("ok"))

        # Desktop reset window endpoint returns 200 JSON
        with patch("anibase.desktop.shell.reset_desktop_window", return_value={"ok": True}):
            res_reset = self.client.post("/api/desktop/reset-window", headers={"X-Action-Token": token})
            self.assertEqual(res_reset.status_code, 200)
            self.assertTrue(res_reset.get_json().get("ok"))

        # Desktop shutdown endpoint returns 200 JSON (patched thread to avoid stopping test runner)
        with patch("threading.Thread"), \
             patch("anibase.desktop.shell.close_desktop_window_permanent"):
            res_shutdown = self.client.post("/api/desktop/shutdown", headers={"X-Action-Token": token})
            self.assertEqual(res_shutdown.status_code, 200)
            self.assertTrue(res_shutdown.get_json().get("ok"))

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

    def test_configure_webview2_enhanced_settings(self):
        mock_win = MagicMock()
        mock_core = MagicMock()
        mock_settings = MagicMock()
        mock_core.Settings = mock_settings
        mock_webview_ctrl = MagicMock()
        mock_webview_ctrl.CoreWebView2 = mock_core
        mock_win.native.webview = mock_webview_ctrl
        mock_win.native.InvokeRequired = False

        with patch("os.name", "nt"):
            res = desktop_shell.configure_webview2_settings(mock_win)
        self.assertTrue(res)
        self.assertTrue(mock_settings.AreBrowserAcceleratorKeysEnabled)
        self.assertFalse(mock_settings.IsZoomControlEnabled)
        self.assertFalse(mock_settings.IsStatusBarEnabled)
        self.assertFalse(mock_settings.IsSwipeNavigationEnabled)
        self.assertFalse(mock_settings.AreDefaultContextMenusEnabled)

    def test_restore_desktop_window_win32_calls(self):
        mock_win = MagicMock()
        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell.get_window_hwnd", return_value=54321), \
             patch("os.name", "nt"), \
             patch("ctypes.windll", create=True) as mock_windll:
            mock_user32 = MagicMock()
            mock_windll.user32 = mock_user32
            mock_user32.GetForegroundWindow.return_value = 54321
            mock_win.show = MagicMock()
            mock_win.restore = MagicMock()

            desktop_shell._window_state["maximized"] = False
            res = desktop_shell.restore_desktop_window()
            self.assertTrue(res)
            mock_win.show.assert_called_once()
            mock_win.restore.assert_called_once()
            mock_user32.ShowWindow.assert_called_once_with(54321, 9)
            mock_user32.SetForegroundWindow.assert_called_once_with(54321)
            mock_user32.BringWindowToTop.assert_called_once_with(54321)

    def test_desktop_titlebar_css_spacing_and_spacer(self):
        css_path = os.path.join(build.PROJECT_ROOT, "static", "desktop-titlebar.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css_text = f.read()
        self.assertIn(".desktop-titlebar-spacer", css_text)
        self.assertIn("body.is-desktop-app #anibaseTopbar", css_text)
        self.assertIn("top: 42px", css_text)

    def test_reset_desktop_window(self):
        mock_win = MagicMock()
        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell.get_screen_bounds", return_value=(1920, 1080)):
            res = desktop_shell.reset_desktop_window()
            self.assertTrue(res["ok"])
            self.assertEqual(res["width"], 1280)
            self.assertEqual(res["height"], 800)
            self.assertEqual(res["x"], 320)
            self.assertEqual(res["y"], 140)
            mock_win.restore.assert_called_once()
            mock_win.resize.assert_called_once_with(1280, 800)
            mock_win.move.assert_called_once_with(320, 140)

    def test_desktop_api_reset_window_call(self):
        with patch("anibase.desktop.shell.reset_desktop_window", return_value={"ok": True}) as mock_reset:
            api = DesktopApi(MagicMock())
            res = api.reset_window()
            mock_reset.assert_called_once()
            self.assertTrue(res["ok"])

    def test_windows_startup_functions(self):
        mock_winreg = MagicMock()
        mock_winreg.HKEY_CURRENT_USER = 1
        mock_winreg.KEY_READ = 1
        mock_winreg.KEY_SET_VALUE = 2
        mock_winreg.REG_SZ = 1
        mock_winreg.QueryValueEx.return_value = ('"C:\\app.exe"', 1)
        with patch("os.name", "nt"), \
             patch.dict(sys.modules, {"winreg": mock_winreg}):
            
            # Test query enabled
            self.assertTrue(desktop_shell.is_windows_startup_enabled())

            # Test enable startup
            ok_enable = desktop_shell.set_windows_startup(True)
            self.assertTrue(ok_enable)
            mock_winreg.SetValueEx.assert_called()

            # Test disable startup
            ok_disable = desktop_shell.set_windows_startup(False)
            self.assertTrue(ok_disable)
            mock_winreg.DeleteValue.assert_called()

    def test_is_system_startup_enabled_dispatches_by_platform(self):
        """is_system_startup_enabled() delegates to Windows or Linux helper correctly."""
        with patch("anibase.desktop.shell.os") as mock_os:
            mock_os.name = "nt"
            with patch.object(desktop_shell, "is_windows_startup_enabled", return_value=True) as mock_win:
                result = desktop_shell.is_system_startup_enabled()
                mock_win.assert_called_once()
                self.assertTrue(result)

            mock_os.name = "posix"
            with patch.object(desktop_shell, "is_linux_startup_enabled", return_value=False) as mock_lin:
                result = desktop_shell.is_system_startup_enabled()
                mock_lin.assert_called_once()
                self.assertFalse(result)

    def test_set_system_startup_dispatches_by_platform(self):
        """set_system_startup() delegates to Windows or Linux helper correctly."""
        with patch("anibase.desktop.shell.os") as mock_os:
            mock_os.name = "nt"
            with patch.object(desktop_shell, "set_windows_startup", return_value=True) as mock_win:
                desktop_shell.set_system_startup(True)
                mock_win.assert_called_once_with(True)

            mock_os.name = "posix"
            with patch.object(desktop_shell, "set_linux_startup", return_value=True) as mock_lin:
                desktop_shell.set_system_startup(True)
                mock_lin.assert_called_once_with(True)

    def test_linux_autostart_enable_creates_desktop_file(self):
        """set_linux_startup(True) writes a valid .desktop file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            desktop_path = os.path.join(tmpdir, "autostart", "anibase.desktop")
            with patch.object(desktop_shell, "_get_linux_autostart_path", return_value=desktop_path), \
                 patch.object(desktop_shell, "_get_linux_startup_command", return_value='"/usr/bin/anibase"'), \
                 patch.object(desktop_shell, "_get_linux_icon_path", return_value="anibase"):
                result = desktop_shell.set_linux_startup(True)
                self.assertTrue(result)
                self.assertTrue(os.path.isfile(desktop_path))
                with open(desktop_path, encoding="utf-8") as fh:
                    content = fh.read()
                self.assertIn("[Desktop Entry]", content)
                self.assertIn("X-GNOME-Autostart-enabled=true", content)
                self.assertIn('"/usr/bin/anibase"', content)

    def test_linux_autostart_disable_removes_desktop_file(self):
        """set_linux_startup(False) removes the .desktop file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            desktop_path = os.path.join(tmpdir, "autostart", "anibase.desktop")
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, "w") as f:
                f.write("[Desktop Entry]\nType=Application\n")
            with patch.object(desktop_shell, "_get_linux_autostart_path", return_value=desktop_path):
                result = desktop_shell.set_linux_startup(False)
                self.assertTrue(result)
                self.assertFalse(os.path.isfile(desktop_path))

    def test_is_linux_startup_enabled_true(self):
        """is_linux_startup_enabled() returns True when valid .desktop file exists."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            desktop_path = os.path.join(tmpdir, "anibase.desktop")
            with open(desktop_path, "w") as f:
                f.write("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=true\n")
            with patch.object(desktop_shell, "_get_linux_autostart_path", return_value=desktop_path):
                self.assertTrue(desktop_shell.is_linux_startup_enabled())

    def test_is_linux_startup_enabled_false_when_explicitly_disabled(self):
        """is_linux_startup_enabled() returns False when X-GNOME-Autostart-enabled=false."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            desktop_path = os.path.join(tmpdir, "anibase.desktop")
            with open(desktop_path, "w") as f:
                f.write("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=false\n")
            with patch.object(desktop_shell, "_get_linux_autostart_path", return_value=desktop_path):
                self.assertFalse(desktop_shell.is_linux_startup_enabled())

    def test_is_linux_startup_enabled_false_when_missing(self):
        """is_linux_startup_enabled() returns False when .desktop file is absent."""
        with patch.object(desktop_shell, "_get_linux_autostart_path", return_value="/nonexistent/path/anibase.desktop"):
            self.assertFalse(desktop_shell.is_linux_startup_enabled())

    def test_get_screen_bounds_fallback(self):
        """get_screen_bounds() falls back to (1920, 1080) when all detection fails."""
        with patch("anibase.desktop.shell.os") as mock_os, \
             patch.dict("sys.modules", {"webview": None, "PySide6": None, "PySide6.QtWidgets": None}):
            mock_os.name = "posix"
            # Both webview and PySide6 unavailable → should return fallback
            result = desktop_shell.get_screen_bounds()
            self.assertEqual(result, (1920, 1080))

    def test_ensure_kde_kwin_rule_applies_noborder(self):
        """ensure_kde_kwin_rule() configures noborder rule on KDE Plasma."""
        with patch("anibase.desktop.shell.os", wraps=os) as mock_os, \
             patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}), \
             patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value="/usr/bin/kwriteconfig6"), \
             patch("subprocess.run") as mock_run:
            mock_os.name = "posix"
            desktop_shell.ensure_kde_kwin_rule()
            self.assertTrue(mock_run.called)

    def test_api_desktop_reset_window_and_status_endpoints(self):
        app = create_app()
        client = app.test_client()
        with patch("anibase.settings.get_action_token", return_value="test-desktop-token"):
            with patch("anibase.desktop.shell.reset_desktop_window", return_value={"ok": True, "width": 1280}):
                res_reset = client.post("/api/desktop/reset-window", headers={"X-Action-Token": "test-desktop-token"})
                self.assertEqual(res_reset.status_code, 200)
                data_reset = res_reset.get_json()
                self.assertTrue(data_reset["ok"])

        with patch("anibase.desktop.shell.is_system_startup_enabled", return_value=False):
            res_status = client.get("/api/desktop/status")
            self.assertEqual(res_status.status_code, 200)
            data_status = res_status.get_json()
            self.assertTrue(data_status["ok"])
            self.assertIn("is_desktop", data_status)
            self.assertIn("run_on_startup", data_status)
            self.assertIn("app_version", data_status)

        with patch("anibase.desktop.updater.check_github_release", return_value={"ok": True, "update_available": False}):
            res_update = client.get("/api/desktop/check-update")
            self.assertEqual(res_update.status_code, 200)
            data_update = res_update.get_json()
            self.assertTrue(data_update["ok"])
            self.assertFalse(data_update["update_available"])

    def test_desktop_api_check_for_updates(self):
        api = desktop_shell.DesktopApi()
        with patch("anibase.desktop.updater.check_github_release", return_value={"ok": True, "update_available": True}):
            res = api.check_for_updates(force=True)
            self.assertTrue(res["ok"])
            self.assertTrue(res["update_available"])

    def test_update_settings_persists_desktop_run_on_startup(self):
        app = create_app()
        client = app.test_client()
        token = get_action_token()

        with patch("anibase.web.routes_api.load_settings") as mock_load, \
             patch("anibase.web.routes_api.save_settings") as mock_save, \
             patch("anibase.web.routes_api.apply_settings"), \
             patch("anibase.web.routes_api.reconfigure_library_observer"), \
             patch("anibase.web.routes_api.start_auto_import_worker"), \
             patch("anibase.web.routes_api.sync_all_library"):

            mock_load.return_value = {
                "action_token": token,
                "desktop_minimize_to_tray": True,
                "desktop_start_minimized": False,
                "desktop_run_on_startup": False
            }

            resp = client.post("/settings", data={
                "action_token": token,
                "desktop_settings_present": "1",
                "desktop_minimize_to_tray": "on",
                "desktop_run_on_startup": "on"
            }, headers={"X-Action-Token": token})

            self.assertEqual(resp.status_code, 302)
            saved = mock_save.call_args[0][0]
            self.assertTrue(saved.get("desktop_run_on_startup"))
            self.assertTrue(saved.get("desktop_minimize_to_tray"))
            self.assertFalse(saved.get("desktop_start_minimized"))


class LinuxDesktopShellCompatibilityTests(unittest.TestCase):
    """Tests for PyWebView GUI engine selection, storage paths, and GNOME/Linux shell compatibility."""

    def test_get_desktop_gui_engine_windows(self):
        with patch("anibase.desktop.shell.os.name", "nt"), \
             patch.dict("os.environ", {}, clear=True):
            self.assertEqual(desktop_shell.get_desktop_gui_engine(), "edgechromium")

    def test_get_desktop_gui_engine_override(self):
        with patch.dict("os.environ", {"ANIBASE_WEBVIEW_GUI": "gtk"}):
            self.assertEqual(desktop_shell.get_desktop_gui_engine(), "gtk")

        with patch.dict("os.environ", {"ANIBASE_WEBVIEW_GUI": "qt"}):
            self.assertEqual(desktop_shell.get_desktop_gui_engine(), "qt")

    def test_get_desktop_storage_path(self):
        with patch("anibase.desktop.shell.os.name", "nt"), \
             patch.dict("os.environ", {"LOCALAPPDATA": "C:\\MockAppData"}), \
             patch("os.makedirs"):
            path = desktop_shell.get_desktop_storage_path()
            self.assertTrue(path.startswith("C:\\MockAppData"))

        with patch("anibase.desktop.shell.os.name", "posix"), \
             patch("anibase.utils.get_user_data_dir", return_value="/home/user/.local/share/AniBase"), \
             patch("os.makedirs"):
            path = desktop_shell.get_desktop_storage_path()
            self.assertEqual(path.replace("\\", "/"), "/home/user/.local/share/AniBase/webview")

    def test_system_tray_available_flag(self):
        desktop_shell.set_system_tray_available(False)
        self.assertFalse(desktop_shell.is_system_tray_available())
        desktop_shell.set_system_tray_available(True)
        self.assertTrue(desktop_shell.is_system_tray_available())

    def test_restore_desktop_window_linux_presents_gtk_window(self):
        mock_win = MagicMock()
        mock_native = MagicMock()
        mock_win.native = mock_native

        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell.os.name", "posix"):
            res = desktop_shell.restore_desktop_window()
            self.assertTrue(res)
            mock_win.show.assert_called_once()
            mock_native.present.assert_called_once()

    def test_find_chromium_browser_linux(self):
        import tray_ui
        with patch("tray_ui.os.name", "posix"), \
             patch("tray_ui.shutil.which", side_effect=lambda name: "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None):
            browser = tray_ui.find_chromium_browser()
            self.assertEqual(browser, "/usr/bin/google-chrome-stable")

    def test_get_linux_icon_path_prioritizes_favicon_ico(self):
        with patch("anibase.desktop.shell.get_resource_dir", return_value=os.path.abspath(".")):
            icon = desktop_shell._get_linux_icon_path()
            self.assertTrue(os.path.isfile(icon))
            self.assertEqual(os.path.basename(icon), "favicon.ico")

    def test_tray_get_icon_path_prioritizes_favicon_ico(self):
        import tray_ui
        with patch.object(tray_ui, "RESOURCE_DIR", os.path.abspath("."), create=True):
            icon = tray_ui.get_icon_path()
            self.assertTrue(os.path.isfile(icon))
            self.assertEqual(os.path.basename(icon), "favicon.ico")

    def test_ensure_linux_desktop_entry_replaces_stale_icon_with_favicon_png(self):
        import tempfile
        from anibase.utils import ico_to_png_bytes

        expected_png = ico_to_png_bytes(os.path.abspath("static/favicon.ico"))
        self.assertIsNotNone(expected_png)
        self.assertTrue(expected_png.startswith(b"\x89PNG\r\n\x1a\n"))

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_icon = os.path.join(tmpdir, ".local", "share", "icons", "hicolor", "512x512", "apps", "anibase.png")
            os.makedirs(os.path.dirname(dest_icon), exist_ok=True)
            with open(dest_icon, "wb") as f:
                f.write(b"stale-newer-icon")

            with patch("anibase.desktop.shell.os.name", "posix"), \
                 patch("anibase.desktop.shell.os.path.expanduser", return_value=tmpdir), \
                 patch("anibase.desktop.shell.ensure_kde_kwin_rule"), \
                 patch("anibase.desktop.shell.get_resource_dir", return_value=os.path.abspath(".")), \
                 patch("anibase.utils.get_application_dir", return_value=os.path.abspath(".")), \
                 patch("subprocess.run"):
                desktop_shell.ensure_linux_desktop_entry()

            with open(dest_icon, "rb") as f:
                self.assertEqual(f.read(), expected_png)

    def test_ico_to_png_bytes_pure_python_fallback(self):
        import builtins
        from anibase.utils import ico_to_png_bytes

        orig_import = builtins.__import__

        def block_pil(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return orig_import(name, globals, locals, fromlist, level)

        with patch.object(builtins, "__import__", side_effect=block_pil):
            png_data = ico_to_png_bytes(os.path.abspath("static/favicon.ico"))
        self.assertIsNotNone(png_data)
        self.assertTrue(png_data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_tray_start_background_services_starts_watchlist_tracker(self):
        import tray_ui
        with patch("tray_ui.threading.Thread"), \
             patch("tray_ui.start_auto_import_worker"), \
             patch("tray_ui.start_watchlist_tracker_worker") as mock_tracker, \
             patch("tray_ui.log_startup_summary"):
            tray_ui.start_background_services()
            mock_tracker.assert_called_once()

    def test_tray_local_action_headers_include_action_token(self):
        import tray_ui
        with patch("anibase.settings.get_action_token", return_value="tray-secret-token"):
            headers = tray_ui._get_local_action_headers()
            self.assertEqual(headers.get("X-AniBase-Action-Token"), "tray-secret-token")
            self.assertEqual(headers.get("X-Action-Token"), "tray-secret-token")

    def test_monitor_tray_helper_stream_syncs_availability(self):
        import io
        import tray_ui
        orig = desktop_shell._system_tray_available
        try:
            desktop_shell._system_tray_available = None
            tray_ui._monitor_tray_helper_stream(io.StringIO("Qt warning\nANIBASE_TRAY_AVAILABLE=0\n"))
            self.assertFalse(desktop_shell.is_system_tray_available())

            tray_ui._monitor_tray_helper_stream(io.StringIO("ANIBASE_TRAY_AVAILABLE=1\n"))
            self.assertTrue(desktop_shell.is_system_tray_available())

            tray_ui._monitor_tray_helper_stream(io.StringIO(""))
            self.assertFalse(desktop_shell.is_system_tray_available())
        finally:
            desktop_shell._system_tray_available = orig

    def test_windows_startup_command_uses_tray_ui_when_not_frozen(self):
        with patch.object(sys, "frozen", False, create=True), \
             patch("anibase.utils.get_application_dir", return_value="/mock/AniBase"):
            cmd = desktop_shell.get_windows_startup_command()
            self.assertIn("tray_ui.py", cmd)
            self.assertNotIn("main.py", cmd)

    def test_load_saved_window_state_supports_multimonitor_and_ignores_minimized_sentinel(self):
        # 1. Negative coordinates on left secondary monitor are preserved when within virtual desktop
        saved_left = {"desktop_window": {"x": -1200, "y": 100, "width": 1280, "height": 800}}
        with patch("anibase.desktop.shell.load_settings", return_value=saved_left), \
             patch("anibase.desktop.shell.get_screen_bounds", return_value=(1920, 1080)), \
             patch("anibase.desktop.shell._get_windows_virtual_screen_bounds", return_value=(-1920, 0, 3840, 1080)):
            load_saved_window_state()
            self.assertEqual(desktop_shell._window_state["x"], -1200)
            self.assertEqual(desktop_shell._window_state["y"], 100)

        # 2. Win32 minimized sentinel (-32000, -32000) falls back to centered primary bounds
        saved_min = {"desktop_window": {"x": -32000, "y": -32000, "width": 1280, "height": 800}}
        with patch("anibase.desktop.shell.load_settings", return_value=saved_min), \
             patch("anibase.desktop.shell.get_screen_bounds", return_value=(1920, 1080)), \
             patch("anibase.desktop.shell._get_windows_virtual_screen_bounds", return_value=(-1920, 0, 3840, 1080)):
            load_saved_window_state()
            self.assertEqual(desktop_shell._window_state["x"], 320)
            self.assertEqual(desktop_shell._window_state["y"], 140)

    def test_window_state_ignores_minimized_coordinates_on_save_and_move(self):
        desktop_shell._window_state.update({
            "x": 250, "y": 120, "width": 1280, "height": 800,
            "maximized": False, "fullscreen": False,
        })
        desktop_shell.on_window_moved(-32000, -32000)
        self.assertEqual(desktop_shell._window_state["x"], 250)
        self.assertEqual(desktop_shell._window_state["y"], 120)

        mock_win = MagicMock()
        mock_win.width = 160
        mock_win.height = 28
        mock_win.x = -32000
        mock_win.y = -32000
        saved = {}
        with patch("anibase.desktop.shell.desktop_window", mock_win), \
             patch("anibase.desktop.shell._is_windows_window_minimized", return_value=True), \
             patch("anibase.desktop.shell.load_settings", return_value={}), \
             patch("anibase.desktop.shell.save_settings", side_effect=saved.update):
            save_current_window_state()
        self.assertEqual(saved["desktop_window"]["x"], 250)
        self.assertEqual(saved["desktop_window"]["width"], 1280)

    def test_perform_graceful_shutdown_is_idempotent(self):
        import tray_ui
        orig_done = tray_ui._shutdown_completed
        try:
            tray_ui._shutdown_completed = False
            with patch("anibase.desktop.shell.close_desktop_window_permanent") as mock_close, \
                 patch.object(tray_ui, "app_log", MagicMock()), \
                 patch.object(tray_ui, "request_shutdown", MagicMock()) as mock_req, \
                 patch.object(tray_ui, "stop_library_observer", MagicMock()), \
                 patch.object(tray_ui, "shutdown_flask_server", MagicMock()) as mock_flask, \
                 patch.object(tray_ui, "join_background_workers", MagicMock()):
                tray_ui.perform_graceful_shutdown()
                tray_ui.perform_graceful_shutdown()
            mock_close.assert_called_once()
            mock_req.assert_called_once()
            mock_flask.assert_called_once()
        finally:
            tray_ui._shutdown_completed = orig_done

    def test_api_desktop_check_update_post_requires_action_token_and_forces_check(self):
        app = create_app()
        client = app.test_client()
        token = get_action_token()

        res_missing = client.post("/api/desktop/check-update?force=1")
        self.assertEqual(res_missing.status_code, 403)
        self.assertEqual(res_missing.get_json()["error"], "invalid_action_token")

        payload = {
            "ok": True,
            "cached": False,
            "current_version": "1.0.0",
            "latest_version": "9.9.9",
            "release_name": "AniBase v9.9.9",
            "published_at": "2026-11-28T00:00:00Z",
            "update_available": True,
            "release_url": "https://github.com/anzutm/anibase-local/releases/tag/v9.9.9",
            "release_notes": "New release!",
            "last_checked_at": 1700000000.0,
            "has_checked": True,
            "github_repo": "anzutm/anibase-local",
            "notified": False,
        }
        with patch("anibase.desktop.updater.check_github_release", return_value=payload) as mock_check:
            res_ok = client.post(
                "/api/desktop/check-update?force=1",
                headers={"X-AniBase-Action-Token": token},
            )
            self.assertEqual(res_ok.status_code, 200)
            body = res_ok.get_json()
            self.assertTrue(body["ok"])
            self.assertTrue(body["update_available"])
            self.assertEqual(body["latest_version"], "9.9.9")
            mock_check.assert_called_once_with(force=True, notify=True)


if __name__ == "__main__":
    unittest.main()
