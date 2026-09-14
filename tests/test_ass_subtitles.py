"""Original fansub typesetting must bypass the lossy plain-caption path."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


ASS_FIXTURE = r"""[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 2,0:00:00.00,0:00:05.00,Default,,0,0,0,,{\an7\pos(400,80)\frz5}PHONE CHAT
Dialogue: 1,0:00:06.00,0:00:09.00,Default,,0,0,0,,{\an7\move(40,80,80,80)\k40}Moving text
"""


class AssSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / 'Episode.mkv'
        self.video.write_bytes(b'video')
        self.cache = self.root / 'cache'
        self.streams = [
            {'index': 2, 'codec_name': 'ass', 'codec_type': 'subtitle', 'tags': {'language': 'ind'}},
            {'index': 3, 'codec_type': 'attachment', 'extradata_size': 100,
             'tags': {'filename': '../../font.ttf'}},
        ]

    def probe(self, *args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({'streams': self.streams}))

    def convert(self, args, **kwargs):
        if '-dump_attachment:3' in args:
            Path(args[args.index('-dump_attachment:3') + 1]).write_bytes(b'font')
        else:
            self.assertEqual(args[args.index('-c:s') + 1], 'copy')
            Path(args[-1]).write_text(ASS_FIXTURE, encoding='utf-8')
        return {'ok': True}

    def assets(self):
        return main.get_ass_subtitle_assets(str(self.video), str(self.cache))

    def test_original_coordinates_layers_and_safe_font_names_are_preserved(self):
        with patch.object(main, 'run_hidden_subprocess', side_effect=self.probe), \
             patch.object(main, 'run_ffmpeg_command', side_effect=self.convert), \
             patch.object(main, 'clean_generated_subtitle_vtt') as cleaner:
            folder, manifest = self.assets()
        self.assertEqual(manifest['renderer'], 'ass')
        self.assertEqual(manifest['fonts'], ['font-3.ttf'])
        self.assertEqual(Path(folder, 'track.ass').read_text(encoding='utf-8'), ASS_FIXTURE)
        cleaner.assert_not_called()

    def test_cache_skips_probe_and_refreshes_when_sidecar_added_or_removed(self):
        with patch.object(main, 'run_hidden_subprocess', side_effect=self.probe) as probe, \
             patch.object(main, 'run_ffmpeg_command', side_effect=self.convert):
            first = self.assets()
            self.assertEqual(self.assets(), first)
            self.assertEqual(probe.call_count, 1)
            sidecar = self.root / 'Episode.ass'
            sidecar.write_text(ASS_FIXTURE)
            # Even an older sidecar changes source selection and invalidates the cache.
            os.utime(sidecar, (1, 1))
            second = self.assets()
            self.assertNotEqual(first[0], second[0])
            sidecar.unlink()
            self.assertEqual(self.assets(), first)

    def test_plain_sidecar_uses_native_caption_fallback_without_probe(self):
        (self.root / 'Episode.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nHello\n')
        with patch.object(main, 'run_hidden_subprocess') as probe:
            _, manifest = self.assets()
            self.assertEqual(manifest['renderer'], 'vtt')
            probe.assert_not_called()

    def test_failed_extraction_is_not_published_as_cached_success(self):
        with patch.object(main, 'run_hidden_subprocess', side_effect=self.probe), \
             patch.object(main, 'run_ffmpeg_command', return_value={'ok': False}):
            with self.assertRaises(RuntimeError):
                self.assets()
        self.assertEqual(list(self.cache.rglob('manifest.json')), [])

    def test_manifest_and_assets_are_scoped_to_selected_episode(self):
        with patch.object(main, 'is_setup_complete', return_value=True), \
             patch.object(main, 'find_media_path', return_value=str(self.root)), \
             patch.object(main, 'SUBTITLE_CACHE', str(self.cache)), \
             patch.object(main, 'run_hidden_subprocess', side_effect=self.probe), \
             patch.object(main, 'run_ffmpeg_command', side_effect=self.convert):
            client = main.app.test_client()
            manifest = client.get('/subtitle/Example/Episode.mkv?format=manifest')
            self.assertEqual(manifest.status_code, 200)
            self.assertEqual(manifest.headers['Cache-Control'], 'no-store')
            data = manifest.get_json()
            ass = client.get(data['subtitle'])
            self.assertEqual(ass.status_code, 200)
            self.assertIn(b'\\pos(400,80)', ass.data)
            ass.close()
            with client.get(data['fonts'][0]) as font:
                self.assertEqual(font.data, b'font')
            self.assertEqual(client.get('/subtitle/Example/Episode.mkv?format=asset&asset=../../main.py').status_code, 404)
            self.assertEqual(client.get('/subtitle/Example/../outside.mkv?format=manifest').status_code, 404)


if __name__ == '__main__':
    unittest.main()
