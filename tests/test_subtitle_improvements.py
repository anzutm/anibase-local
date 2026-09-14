import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import main


class SubtitleImprovementsTests(unittest.TestCase):
    def test_selection_skips_bitmap_and_signs_for_indonesian_dialogue(self):
        streams = [
            {'index': 2, 'codec_name': 'hdmv_pgs_subtitle'},
            {'index': 3, 'codec_name': 'ass', 'tags': {'language': 'ind', 'title': 'Signs & Songs'}, 'disposition': {'default': 1}},
            {'index': 4, 'codec_name': 'subrip', 'tags': {'language': 'eng'}},
            {'index': 5, 'codec_name': 'ass', 'tags': {'language': 'ind', 'title': 'Full dialogue'}},
        ]
        with patch.object(main, 'run_hidden_subprocess', return_value=SimpleNamespace(stdout=json.dumps({'streams': streams}))):
            self.assertEqual(main.select_subtitle_stream('video.mkv'), '0:5')

    def test_sidecar_matching_and_cache_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder, 'Episode 01.mkv')
            video.write_bytes(b'video')
            Path(folder, 'Episode 010.ass').write_text('unrelated')
            self.assertIsNone(main.find_external_subtitle(str(video)))
            sidecar = Path(folder, 'Episode 01.ind.ass')
            sidecar.write_text('subtitle')
            Path(folder, 'Episode 01.eng.srt').write_text('English')
            self.assertEqual(main.find_external_subtitle(str(video)), str(sidecar))
            cache = Path(folder, 'cache.vtt')
            cache.write_text('WEBVTT\n')
            self.assertTrue(main.subtitle_cache_is_current(str(video), str(cache)))
            newer = cache.stat().st_mtime_ns + 1000000000
            os.utime(sidecar, ns=(newer, newer))
            self.assertFalse(main.subtitle_cache_is_current(str(video), str(cache)))

    def test_invalid_duplicate_and_styled_dialogue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'sub.vtt')
            path.write_text(
                '\ufeffWEBVTT\n\n'
                '00:00:04.000 --> 00:00:03.000\nInvalid\n\n'
                '00:00:01.000 --> 00:00:03.000\n{\\an8}<i>Hello</i>\\NVisit example.com today.\n\n'
                '00:00:01.000 --> 00:00:03.000\n{\\an8}<i>Hello</i>\\NVisit example.com today.\n\n'
                '00:00:07.000 -->\nBroken timing\n', encoding='utf-8')
            main.clean_generated_subtitle_vtt(str(path))
            result = path.read_text(encoding='utf-8')
            self.assertEqual(result.count('<i>Hello</i>'), 1)
            self.assertIn('<i>Hello</i>\nVisit example.com today.', result)
            self.assertNotIn('Invalid', result)
            self.assertNotIn('Broken timing', result)

    def test_sidecar_is_converted_without_probing_video(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder, 'Episode.mkv')
            video.write_bytes(b'video')
            sidecar = Path(folder, 'Episode.srt')
            sidecar.write_text('1\n00:00:01,000 --> 00:00:02,000\nHello\n')
            def convert(args, **kwargs):
                self.assertEqual(args[args.index('-i') + 1], str(sidecar))
                Path(args[-1]).write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n')
                return {'ok': True}
            with patch.object(main, 'run_ffmpeg_command', side_effect=convert), patch.object(main, 'select_subtitle_stream') as probe:
                result = main.generate_subtitle_vtt_result(str(video), str(Path(folder, 'output.vtt')))
                self.assertTrue(result['ok'])
                probe.assert_not_called()
