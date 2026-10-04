import unittest
import json
import os
import shutil
import tempfile
from unittest.mock import patch

import main
from main import (
    app,
    get_default_settings,
    load_settings,
    save_settings,
    normalize_shortcuts,
    DEFAULT_PLAYER_SHORTCUTS,
    SHORTCUT_DEFINITIONS,
)


class TestShortcuts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patch_settings_file = patch.object(
            main, "SETTINGS_FILE", os.path.join(self.temp_dir, "settings.json")
        )
        self.patch_settings_file.start()
        settings = get_default_settings()
        settings["setup_completed"] = True
        settings["library_paths"] = [self.temp_dir]
        save_settings(settings)
        self.client = app.test_client()
        self.client.testing = True

    def tearDown(self):
        self.patch_settings_file.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_shortcuts_definitions(self):
        self.assertIsInstance(SHORTCUT_DEFINITIONS, list)
        self.assertGreater(len(SHORTCUT_DEFINITIONS), 0)

        expected_actions = {
            "toggle_play",
            "toggle_fullscreen",
            "toggle_mute",
            "toggle_subtitles",
            "seek_backward",
            "seek_forward",
            "seek_backward_large",
            "seek_forward_large",
            "previous_episode",
            "next_episode",
            "volume_up",
            "volume_down",
            "speed_up",
            "speed_down",
            "aniskip",
            "screenshot",
            "shortcut_help",
        }

        all_actions = set()
        for cat in SHORTCUT_DEFINITIONS:
            self.assertIn("category", cat)
            self.assertIn("category_id", cat)
            self.assertIn("actions", cat)
            for action in cat["actions"]:
                self.assertIn("id", action)
                self.assertIn("label", action)
                self.assertIn("default", action)
                all_actions.add(action["id"])

        self.assertTrue(expected_actions.issubset(all_actions))
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["toggle_play"], "Space")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["toggle_fullscreen"], "f")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["seek_backward"], "ArrowLeft")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["seek_forward"], "ArrowRight")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["seek_backward_large"], "Ctrl+ArrowLeft")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["seek_forward_large"], "Ctrl+ArrowRight")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["previous_episode"], "Shift+P")
        self.assertEqual(DEFAULT_PLAYER_SHORTCUTS["next_episode"], "Shift+N")

    def test_normalize_shortcuts(self):
        # Non-dict fallback
        normalized = normalize_shortcuts(None)
        self.assertEqual(normalized, DEFAULT_PLAYER_SHORTCUTS)

        # Empty dict fallback
        normalized = normalize_shortcuts({})
        self.assertEqual(normalized, DEFAULT_PLAYER_SHORTCUTS)

        # Custom values preserved and trimmed
        custom = {
            "toggle_play": "k",
            "seek_backward": "  j  ",
            "volume_up": "",  # Empty string should fallback
        }
        normalized = normalize_shortcuts(custom)
        self.assertEqual(normalized["toggle_play"], "k")
        self.assertEqual(normalized["seek_backward"], "j")
        self.assertEqual(normalized["volume_up"], DEFAULT_PLAYER_SHORTCUTS["volume_up"])
        self.assertEqual(normalized["toggle_fullscreen"], DEFAULT_PLAYER_SHORTCUTS["toggle_fullscreen"])

    def test_default_settings_include_shortcuts(self):
        defaults = get_default_settings()
        self.assertIn("shortcuts", defaults)
        self.assertEqual(defaults["shortcuts"], DEFAULT_PLAYER_SHORTCUTS)

    def test_load_settings_normalizes_missing_shortcuts(self):
        # Save settings without shortcuts key
        settings_path = main.SETTINGS_FILE
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump({"theme_preset": "dark-blue"}, f)

        loaded = load_settings()
        self.assertIn("shortcuts", loaded)
        self.assertEqual(loaded["shortcuts"]["toggle_play"], "Space")
        self.assertEqual(loaded["shortcuts"]["toggle_fullscreen"], "f")

    def test_settings_page_renders_shortcuts_section(self):
        # Must be local client for host_only decorator
        response = self.client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="settings-shortcuts"', html)
        self.assertIn("Keyboard Shortcuts", html)
        self.assertIn("Play / Pause", html)
        self.assertIn("shortcut_toggle_play", html)
        self.assertIn("shortcut_seek_backward_large", html)
        self.assertIn("Reset All Defaults", html)

    def test_update_settings_saves_custom_shortcuts(self):
        token = main.get_action_token()
        form_data = {
            "action_token": token,
            "theme_preset": "dark-blue",
            "shortcut_toggle_play": "p",
            "shortcut_seek_backward": "a",
            "shortcut_seek_forward": "d",
            "shortcut_seek_backward_large": "Shift+A",
            "shortcut_seek_forward_large": "Shift+D",
            "shortcut_aniskip": "x",
            "shortcut_screenshot": "F9",
        }

        response = self.client.post(
            "/settings",
            data=form_data,
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        saved = load_settings()
        self.assertEqual(saved["shortcuts"]["toggle_play"], "p")
        self.assertEqual(saved["shortcuts"]["seek_backward"], "a")
        self.assertEqual(saved["shortcuts"]["seek_forward"], "d")
        self.assertEqual(saved["shortcuts"]["seek_backward_large"], "Shift+A")
        self.assertEqual(saved["shortcuts"]["seek_forward_large"], "Shift+D")
        self.assertEqual(saved["shortcuts"]["aniskip"], "x")
        self.assertEqual(saved["shortcuts"]["screenshot"], "F9")
        # Unchanged actions remain default
        self.assertEqual(saved["shortcuts"]["toggle_fullscreen"], "f")
        self.assertEqual(saved["shortcuts"]["toggle_mute"], "m")


if __name__ == "__main__":
    unittest.main()
