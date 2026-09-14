import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import main
from flask import Flask, jsonify, send_file
from werkzeug.serving import make_server


class SeekPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video = os.path.join(self.temp.name, "Episode 01.mkv")
        with open(self.video, "wb") as handle:
            handle.write(b"source")
        for name, value in {
            "THUMBNAIL_CACHE": os.path.join(self.temp.name, "thumbnails"),
            "DB_PATH": os.path.join(self.temp.name, "library.db"),
            "SEEK_PREVIEW_ACTIVE": set(), "SEEK_PREVIEW_FAILURES": {},
        }.items():
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder, self.version = main.seek_preview_identity(self.video)

    def make_cache(self, duration=1440):
        _, frames = main.seek_preview_frames(duration)
        os.makedirs(self.folder, exist_ok=True)
        for name in {frame["text"] for frame in frames}:
            with open(os.path.join(self.folder, name), "wb") as handle:
                handle.write(b"jpeg")
        with open(os.path.join(self.folder, "manifest.json"), "w") as handle:
            json.dump({"version": self.version, "frames": frames}, handle)

    def test_timeline_coverage_and_sprite_bounds(self):
        for duration in (1, 10, 11, 1001, 1440, 7200, 36000):
            interval, frames = main.seek_preview_frames(duration)
            self.assertLessEqual(len(frames), 180)
            self.assertEqual(frames[0]["startTime"], 0)
            self.assertEqual(frames[-1]["endTime"], duration)
            for previous, following in zip(frames, frames[1:]):
                self.assertEqual(previous["endTime"], following["startTime"])
            for frame in frames:
                self.assertLessEqual(frame["x"] + frame["w"], 3840)
                self.assertLessEqual(frame["y"] + frame["h"], 2160)
        for duration in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                main.seek_preview_frames(duration)

    def test_cache_requires_current_source_and_all_sprites(self):
        self.make_cache()
        self.assertIsNotNone(main.read_seek_preview(self.folder, self.version))
        os.remove(os.path.join(self.folder, "sprite-02.jpg"))
        self.assertIsNone(main.read_seek_preview(self.folder, self.version))
        with open(self.video, "ab") as handle:
            handle.write(b"changed")
        _, new_version = main.seek_preview_identity(self.video)
        self.assertNotEqual(new_version, self.version)
        self.assertIsNone(main.read_seek_preview(self.folder, new_version))

    def test_worker_deduplicates_and_limits_jobs(self):
        with patch.object(main.threading, "Thread") as thread:
            self.assertEqual(main.request_seek_preview(self.video, self.folder, self.version), "pending")
            main.request_seek_preview(self.video, self.folder, self.version)
            main.request_seek_preview(self.video, self.folder + "other", self.version)
            self.assertEqual(thread.call_count, 1)

    def test_cleanup_removes_preview_and_both_card_thumbnail_versions(self):
        self.make_cache()
        thumbnail = main.get_thumbnail_cache_path(self.video)
        for path in (thumbnail, thumbnail.replace("_v2.jpg", ".jpg")):
            with open(path, "wb") as handle:
                handle.write(b"thumbnail")
        summary = main.make_cache_cleanup_summary()
        main.cleanup_thumbnails_for_video_paths([self.video], summary)
        self.assertFalse(os.path.exists(self.folder))
        self.assertFalse(os.path.exists(thumbnail))
        self.assertEqual(summary["removed_files"], 2)
        self.assertEqual(summary["removed_dirs"], 1)

    def test_failed_generation_releases_slot_and_cleans_partial_files(self):
        def fail(args, timeout):
            with open(args[-1].replace("%02d", "01"), "wb") as handle:
                handle.write(b"partial")
            return {"ok": False}
        main.SEEK_PREVIEW_ACTIVE.add(self.folder)
        with patch.object(main, "get_video_duration_seconds", return_value=1440), \
                patch.object(main, "run_ffmpeg_command", side_effect=fail):
            main.generate_seek_preview(self.video, self.folder, self.version)
        self.assertFalse(main.SEEK_PREVIEW_ACTIVE)
        self.assertEqual(os.listdir(self.folder), [])
        self.assertEqual(main.request_seek_preview(self.video, self.folder, self.version), "unavailable")

    def request(self, query=""):
        with patch.object(main, "is_setup_complete", return_value=True), \
                patch.object(main, "find_media_path", return_value=self.temp.name):
            return main.app.test_client().get("/seek-preview/Example/Episode%2001.mkv" + query)

    def test_cached_endpoint_does_not_generate_or_expose_source_path(self):
        self.make_cache()
        with patch.object(main, "request_seek_preview") as worker:
            response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ready")
        self.assertNotIn(self.temp.name, response.get_data(as_text=True))
        worker.assert_not_called()
        response = self.request("?format=vtt")
        self.assertEqual(response.mimetype, "text/vtt")
        self.assertIn("#xywh=0,0,384,216", response.get_data(as_text=True))
        self.assertIn("00:00:00.000 --> 00:00:10.000", response.get_data(as_text=True))

    def test_pending_returns_immediately_and_assets_are_restricted(self):
        with patch.object(main, "request_seek_preview", return_value="pending"):
            response = self.request()
        self.assertEqual(response.status_code, 202)
        self.make_cache()
        for asset in ("../manifest.json", "manifest.json", "sprite-99.jpg"):
            response = self.request(f"?asset={asset}&v={self.version}")
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.request("?asset=sprite-01.jpg&v=old").status_code, 404)
        response = self.request(f"?asset=sprite-01.jpg&v={self.version}")
        self.assertEqual(response.status_code, 200)
        response.close()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_real_ffmpeg_generates_complete_sheets(self):
        # Low-frame-rate synthetic input covers the 100-frame sheet boundary
        # without decoding an actual episode or touching the user's library.
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=1/10",
                        "-t", "1010", "-c:v", "mpeg4", self.video], check=True,
                       capture_output=True, timeout=30)
        self.folder, self.version = main.seek_preview_identity(self.video)
        with patch.object(main, "get_media_tool_path", side_effect=lambda name: shutil.which(name)):
            main.generate_seek_preview(self.video, self.folder, self.version)
        data = main.read_seek_preview(self.folder, self.version)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["frames"]), 101)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "sprite-02.jpg")))

    @unittest.skipUnless(os.environ.get("ANIBASE_BROWSER_TESTS") == "1", "Opt-in browser test")
    def test_browser_hover_touch_switch_and_failed_preview(self):
        from playwright.sync_api import sync_playwright
        mp4 = os.path.join(self.temp.name, "fixture.mp4")
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12",
                        "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", mp4],
                       check=True, capture_output=True, timeout=30)
        folder, version = main.seek_preview_identity(mp4)
        with patch.object(main, "get_media_tool_path", side_effect=lambda name: shutil.which(name)):
            main.generate_seek_preview(mp4, folder, version)
        frames = main.read_seek_preview(folder, version)["frames"]
        fixture = Flask("seek-preview-browser", static_folder=main.app.static_folder)

        @fixture.get("/")
        def index():
            return '''<!doctype html><html><head>
            <link rel="stylesheet" href="/static/plyr.css">
            <link rel="stylesheet" href="/static/player-redesign.css">
            <style>body{margin:0} .video-player-container{width:100%;max-width:640px}</style>
            </head><body class="player-body"><div class="video-player-container">
            <video id="player" controls muted playsinline><source src="/video/one.mp4" type="video/mp4"></video>
            </div><div id="episodeGrid" class="player-episode-grid">
            <a class="player-episode-card" data-episode-path="one.mp4" data-episode-num="1" href="/">One</a>
            <a class="player-episode-card" data-episode-path="two.mp4" data-episode-num="2" href="/">Two</a>
            </div><button id="scrollLeft"></button><button id="scrollRight"></button>
            <script src="/static/plyr.js"></script><script>
            window.AniBaseSubtitles = class { load() {} };
            window.ANIME_NAME='Example';window.EPISODE_PATH='one.mp4';window.RESUME_TIME=0;
            window.STREAM_URL_TEMPLATE='/video/__EPISODE__';
            window.PREVIEW_URL_TEMPLATE='/preview/__EPISODE__';
            </script><script src="/static/player.js"></script></body></html>'''

        @fixture.get("/video/<name>")
        def video(name):
            return send_file(mp4, mimetype="video/mp4")

        @fixture.get("/preview/<name>")
        def preview(name):
            return jsonify(status="ready", frames=[{**frame, "text": f'/sprite/{name}/{frame["text"]}'}
                                                    for frame in frames])

        @fixture.get("/sprite/<episode>/<name>")
        def sprite(episode, name):
            return send_file(os.path.join(folder, name), mimetype="image/jpeg")

        @fixture.post("/<path:action>")
        def action(action):
            return jsonify(status="success")

        server = make_server("127.0.0.1", 0, fixture, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p, p.chromium.launch(channel="msedge", headless=True) as browser:
                for mobile in (False, True):
                    context = browser.new_context(viewport={"width": 390 if mobile else 900, "height": 700},
                                                  has_touch=mobile, is_mobile=mobile)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    page.evaluate("player.muted = true; player.play()")
                    page.wait_for_function("player.previewThumbnails?.loaded === true")
                    page.evaluate("player.pause()")
                    before = page.evaluate("player.currentTime")
                    progress = page.locator(".plyr__progress").bounding_box()
                    page.locator('.plyr__progress').hover(position={"x": progress["width"] * .6,
                                                                                "y": progress["height"] / 2})
                    try:
                        page.wait_for_selector(".plyr__preview-thumb--is-shown", timeout=5000)
                    except Exception:
                        self.fail(str({"errors": errors, "preview": page.evaluate("({loaded:player.previewThumbnails.loaded, time:player.previewThumbnails.seekTime, html:player.elements.progress.outerHTML})")}))
                    self.assertAlmostEqual(page.evaluate("player.currentTime"), before, delta=.1)
                    dimensions = page.locator('.plyr__preview-thumb__image-container').bounding_box()
                    self.assertAlmostEqual(dimensions['width'], 291.2 if mobile else 349.44, delta=1)
                    self.assertAlmostEqual(dimensions['height'], 163.8 if mobile else 196.56, delta=1)
                    if mobile:
                        page.evaluate('''() => {
                            const progress = player.elements.progress;
                            progress.dispatchEvent(new Event('touchstart', {bubbles:true}));
                            player.elements.inputs.seek.value = 50;
                            progress.dispatchEvent(new Event('touchmove', {bubbles:true}));
                        }''')
                        page.wait_for_selector(".plyr__preview-scrubbing--is-shown")
                        page.evaluate("player.elements.progress.dispatchEvent(new Event('touchend', {bubbles:true}))")
                    # Multiple replacements must not accumulate Plyr listeners.
                    initial = page.evaluate("player.eventListeners.length")
                    for episode in ("two.mp4", "one.mp4"):
                        page.evaluate("episode => switchEpisode(episode, {autoplay:true, pushState:false})", episode)
                        page.wait_for_function("player.previewThumbnails?.loaded === true")
                        self.assertIn(episode, page.evaluate("player.previewThumbnails.thumbnails[0].frames[0].text"))
                        self.assertEqual(page.locator(".plyr__preview-thumb").count(), 1)
                    # Existing switchEpisode once(canplay) listeners can remain
                    # in Plyr's bookkeeping; the preview must add none.
                    self.assertLessEqual(page.evaluate("player.eventListeners.length"), initial + 4)
                    page.route("**/preview/**", lambda route: route.fulfill(status=503, body="{}"))
                    page.evaluate("switchEpisode('two.mp4', {autoplay:true, pushState:false})")
                    page.wait_for_function("player.currentTime > 2")
                    self.assertEqual(page.locator(".plyr__preview-thumb").count(), 0)
                    self.assertEqual(errors, [])
                    context.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
