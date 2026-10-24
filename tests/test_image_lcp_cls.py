import unittest


class ImageLcpClsTestCase(unittest.TestCase):
    def test_index_template_lcp_and_cls(self):
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Hero image preload
        self.assertIn('<link rel="preload" as="image"', content)
        self.assertIn('url_for(\'banner\', anime_name=featured_slides[0].anime.name)', content)

        # Spotlight hero image priority and dimensions
        self.assertIn('fetchpriority="high"', content)
        self.assertIn('decoding="async"', content)
        self.assertIn('width="1200"', content)

        # Continue watching image dimensions and async decoding
        self.assertIn('loading="lazy" decoding="async" width="280" height="158"', content)

        # Grid poster dimensions and async decoding
        self.assertIn('width="225"', content)
        self.assertIn('height="330"', content)

    def test_anime_detail_template_lcp_and_cls(self):
        with open('templates/anime.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Local font declaration with swap
        self.assertIn('@font-face', content)
        self.assertIn('font-display: swap;', content)
        self.assertIn('InterVariable.woff2', content)

        # Hero preload
        self.assertIn('<link rel="preload" as="image"', content)
        self.assertIn('url_for(\'banner\', anime_name=anime_name)', content)

        # Hero banner & poster high priority
        self.assertIn('class="hero-banner"', content)
        self.assertIn('class="hero-poster"', content)
        self.assertIn('fetchpriority="high"', content)

        # Character and episode images
        self.assertIn('width="60" height="60"', content)
        self.assertIn('width="280"', content)
        self.assertIn('width="160"', content)

    def test_movies_template_lcp_and_cls(self):
        with open('templates/movies.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Hero preload
        self.assertIn('<link rel="preload" as="image"', content)
        self.assertIn('featured_movies[0].banner', content)

        # Hero backdrop
        self.assertIn('fetchpriority="high" decoding="async"', content)

        # Movie library grid cards
        self.assertIn('width="220" height="330"', content)

    def test_external_anime_template_lcp_and_cls(self):
        with open('templates/external_anime.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Hero preload
        self.assertIn('<link rel="preload" as="image"', content)
        self.assertIn('anime.banner or anime.poster', content)

        # Hero backdrop shouldn't be lazy
        self.assertNotIn('<img class="ext-backdrop-img" src="{{ anime.banner }}" alt="" loading="lazy">', content)
        self.assertIn('<img class="ext-backdrop-img" src="{{ anime.banner }}" alt="" fetchpriority="high" decoding="async">', content)

    def test_style_css_optimizations(self):
        with open('static/style.css', 'r', encoding='utf-8') as f:
            content = f.read()

        # No blocking external font @import
        self.assertNotIn('@import url(\'https://fonts.googleapis.com', content)

        # Aspect ratios defined
        self.assertIn('aspect-ratio: 2 / 3;', content)
        self.assertIn('aspect-ratio:16/9;', content)


if __name__ == '__main__':
    unittest.main()
