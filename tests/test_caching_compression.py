import unittest
import gzip
import main


class CachingAndCompressionTestCase(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()

    def test_html_response_compressed_when_gzip_accepted(self):
        resp = self.client.get('/', headers={'Accept-Encoding': 'gzip, deflate'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', resp.headers.get('Vary', ''))
        # Decompressing must succeed and yield valid HTML
        decompressed = gzip.decompress(resp.data).decode('utf-8')
        self.assertIn('<!DOCTYPE html>', decompressed)
        self.assertIn('AniBase', decompressed)
        # Verify significant compression savings
        self.assertLess(len(resp.data), 35000)

    def test_html_response_uncompressed_when_gzip_not_accepted(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.headers.get('Content-Encoding'))
        text = resp.data.decode('utf-8')
        self.assertIn('<!DOCTYPE html>', text)
        self.assertIn('AniBase', text)

    def test_static_css_compressed_and_cached_immutable_when_versioned(self):
        resp = self.client.get('/static/style.css?v=11', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Encoding'), 'gzip')
        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('public', cache_control)
        self.assertIn('max-age=2592000', cache_control)
        self.assertIn('immutable', cache_control)
        # Content length is significantly compressed (< 70KB vs 347KB)
        self.assertLess(len(resp.data), 70000)

    def test_static_asset_unversioned_has_standard_cache(self):
        resp = self.client.get('/static/style.css')
        self.assertEqual(resp.status_code, 200)
        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('public', cache_control)
        self.assertIn('max-age=86400', cache_control)

    def test_binary_font_not_compressed_by_gzip(self):
        resp = self.client.get('/static/vendor/subtitles-octopus/InterVariable.woff2', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.headers.get('Content-Encoding'))
        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('public', cache_control)

    def test_media_poster_cache_control(self):
        resp = self.client.get('/poster/Gimai%20Seikatsu')
        if resp.status_code == 200:
            cache_control = resp.headers.get('Cache-Control', '')
            self.assertIn('public', cache_control)
            self.assertIn('max-age=86400', cache_control)
            self.assertIsNone(resp.headers.get('Content-Encoding'))

    def test_anime_detail_template_no_random_cache_buster(self):
        with open('templates/anime.html', 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertNotIn('range(1, 9999)', content)
        self.assertNotIn('random', content)
        self.assertRegex(content, r"url_for\('static',\s*filename='style\.css',\s*v='\d+'\)")


if __name__ == '__main__':
    unittest.main()
