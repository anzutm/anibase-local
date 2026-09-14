"""Opt-in real FFmpeg + browser test: ANIBASE_BROWSER_TESTS=1 python -m unittest tests.test_ass_browser.

Uses temporary synthetic media only; never reads or modifies the user's library.
Requires Playwright and an installed Edge browser (PLAYWRIGHT_CHANNEL can override).
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path

from flask import Flask, jsonify, send_file
from werkzeug.serving import make_server

import main
from tests.test_ass_subtitles import ASS_FIXTURE


@unittest.skipUnless(os.environ.get('ANIBASE_BROWSER_TESTS') == '1', 'Opt-in browser integration test')
class AssBrowserTests(unittest.TestCase):
    def test_position_resize_cc_seek_switch_and_fallback(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subtitle = root / 'source.ass'
            subtitle.write_text(ASS_FIXTURE, encoding='utf-8')
            video = root / 'fixture.mkv'
            mp4 = root / 'fixture.mp4'
            font = Path(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'arial.ttf')
            attachments = ['-attach', str(font), '-metadata:s:t:0', 'mimetype=application/x-truetype-font'] if font.exists() else []
            result = main.run_ffmpeg_command([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=24:d=10',
                '-i', str(subtitle), '-map', '0:v', '-map', '1:s', '-c:v', 'libx264',
                '-preset', 'ultrafast', '-c:s', 'ass', '-metadata:s:s:0', 'language=ind',
                *attachments, str(video)], timeout=30)
            self.assertTrue(result['ok'], result)
            result = main.run_ffmpeg_command([
                'ffmpeg', '-y', '-i', str(video), '-map', '0:v', '-c:v', 'copy', '-an', '-sn', str(mp4)], timeout=30)
            self.assertTrue(result['ok'], result)
            assets, manifest = main.get_ass_subtitle_assets(str(video), str(root / 'cache'))
            self.assertEqual(manifest['renderer'], 'ass')
            if font.exists():
                self.assertEqual(len(manifest['fonts']), 1)
                self.assertEqual(Path(assets, manifest['fonts'][0]).read_bytes(), font.read_bytes())
            extracted = Path(assets, manifest['subtitle']).read_text(encoding='utf-8-sig')
            self.assertIn(r'\pos(400,80)\frz5', extracted)
            self.assertIn(r'\move(40,80,80,80)\k40', extracted)
            fixture = Flask('ass-browser-fixture', static_folder=str(Path(main.app.static_folder)))

            @fixture.get('/')
            def index():
                return '''<!doctype html><html><head>
                <link rel="stylesheet" href="/static/plyr.css">
                <link rel="stylesheet" href="/static/subtitles.css">
                <style>body {margin:0} #frame {width:640px} video {width:100%}</style>
                </head><body><div id="frame"><video id="video" controls playsinline>
                <source src="/video" type="video/mp4">
                <track kind="subtitles" srclang="und" label="Auto subtitles" default
                    src="data:text/vtt,WEBVTT%0A%0A" data-subtitle-src="/subtitle">
                </video></div>
                <script src="/static/plyr.js"></script><script src="/static/subtitles.js"></script>
                <script>window.player = new Plyr(document.getElementById('video'), {
                    iconUrl:'/static/plyr.svg',captions:{active:true,update:true,language:'und'}});
                window.subs = new AniBaseSubtitles(document.getElementById('video'), player);
                </script></body></html>'''

            @fixture.get('/video')
            def movie():
                return send_file(mp4, mimetype='video/mp4')

            @fixture.get('/subtitle')
            def metadata():
                from flask import request
                if request.args.get('format') != 'manifest':
                    return plain()
                return jsonify(renderer='ass', subtitle='/asset/track.ass', fonts=['/asset/' + f for f in manifest['fonts']])

            @fixture.get('/plain')
            def plain():
                from flask import request
                if request.args.get('format') == 'manifest':
                    return jsonify(renderer='vtt', fonts=[])
                return 'WEBVTT\n\n00:00:00.000 --> 00:00:09.000\nPlain fallback\n', 200, {'Content-Type': 'text/vtt'}

            @fixture.get('/asset/<name>')
            def asset(name):
                return send_file(Path(assets, name))

            server = make_server('127.0.0.1', 0, fixture, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as p, p.chromium.launch(
                    channel=os.environ.get('PLAYWRIGHT_CHANNEL', 'msedge'), headless=True
                ) as browser:
                    page = browser.new_page(viewport={'width': 900, 'height': 700})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.evaluate('(source) => { new Function(source); }',
                                  Path(main.app.static_folder, 'player.js').read_text(encoding='utf-8'))
                    page.wait_for_function("video.dataset.subtitleRenderer === 'ass'")
                    self.assertTrue(page.evaluate("subs.renderer.fallbackFont.endsWith('/static/vendor/subtitles-octopus/InterVariable.woff2')"))
                    page.wait_for_function('video.readyState >= 2')
                    page.evaluate('video.currentTime = 1')
                    bounds_js = '''() => {
                        const c = document.querySelector('.libassjs-canvas-parent canvas');
                        if (!c) return null;
                        const data = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                        let left=c.width,top=c.height,right=-1,bottom=-1;
                        for(let y=0;y<c.height;y++) for(let x=0;x<c.width;x++) {
                            if (data[(y*c.width+x)*4+3]>16) {
                                left=Math.min(left,x);top=Math.min(top,y);right=Math.max(right,x);bottom=Math.max(bottom,y);
                            }
                        }
                        return right<0 ? null : {x:left/c.width,y:top/c.height,w:(right-left)/c.width,h:(bottom-top)/c.height};
                    }'''
                    page.wait_for_function(f'({bounds_js})() !== null')
                    large = page.evaluate(bounds_js)
                    self.assertAlmostEqual(large['x'], 400 / 640, delta=.025)
                    self.assertLess(large['y'], .27)  # Phone text, not bottom-centred native captions.
                    page.evaluate("document.getElementById('frame').style.width = '320px'")
                    page.wait_for_function("parseInt(document.querySelector('.libassjs-canvas-parent canvas').style.width) === 320")
                    page.wait_for_function(f'({bounds_js})() !== null')
                    small = page.evaluate(bounds_js)
                    self.assertAlmostEqual(large['x'], small['x'], delta=.015)
                    self.assertAlmostEqual(large['y'], small['y'], delta=.015)
                    page.evaluate("video.style.height = '400px'; video.style.objectFit = 'contain'")
                    page.wait_for_function('''() => {
                        const canvas = document.querySelector('.libassjs-canvas-parent canvas').getBoundingClientRect();
                        const frame = video.getBoundingClientRect();
                        return Math.abs(canvas.top - frame.top - 110) < 2;
                    }''')
                    page.wait_for_function(f'({bounds_js})() !== null')
                    self.assertAlmostEqual(page.evaluate(bounds_js)['x'], large['x'], delta=.015)
                    page.evaluate('player.toggleCaptions(false)')
                    page.wait_for_function("getComputedStyle(subs.renderer.canvasParent).visibility === 'hidden'")
                    page.evaluate('player.toggleCaptions(true)')
                    page.wait_for_function("getComputedStyle(subs.renderer.canvasParent).visibility === 'visible'")
                    page.evaluate('video.currentTime = 7')
                    page.wait_for_function(f'({bounds_js})()?.x < .2')
                    page.evaluate('video.currentTime = 1')
                    page.wait_for_function(f'({bounds_js})()?.x > .6')
                    page.evaluate("subs.load('/subtitle')")
                    page.wait_for_function("video.dataset.subtitleRenderer === 'ass'")
                    self.assertEqual(page.locator('.libassjs-canvas-parent').count(), 1)
                    page.evaluate("subs.load('/plain')")
                    page.wait_for_function("video.dataset.subtitleRenderer === 'vtt'")
                    self.assertEqual(page.locator('.libassjs-canvas-parent').count(), 0)
                    self.assertEqual(errors, [])
                    page.wait_for_function("video.textTracks[0].cues?.[0]?.text === 'Plain fallback'")
                    # A late response must not reinstall the previous episode's subtitle.
                    page.evaluate("subs.load('/subtitle'); subs.load('/plain')")
                    page.wait_for_function("video.dataset.subtitleRenderer === 'vtt'")
                    self.assertEqual(page.locator('.libassjs-canvas-parent').count(), 0)
                    # A broken worker falls back visibly instead of leaving an empty CC track.
                    page.route('**/subtitles-octopus-worker.js', lambda route: route.abort())
                    page.evaluate("subs.load('/subtitle')")
                    page.wait_for_function("video.dataset.subtitleRenderer === 'vtt'")
                    self.assertIn('basic subtitles', page.locator('.subtitle-renderer-notice').inner_text())
                    page.wait_for_function("video.textTracks[0].cues?.[0]?.text === 'Plain fallback'")
                    self.assertTrue(all('Worker error' in error for error in errors), errors)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
