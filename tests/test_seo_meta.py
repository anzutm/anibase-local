import os
import unittest


class SeoMetadataTestCase(unittest.TestCase):
    def test_base_template_seo_tags(self):
        with open('templates/base.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Title and Meta description
        self.assertIn('<meta name="description" content="{% block meta_description %}', content)
        self.assertIn('AniBase is your private local anime screening room', content)

        # Open Graph
        self.assertIn('<meta property="og:site_name" content="AniBase">', content)
        self.assertIn('<meta property="og:title"', content)
        self.assertIn('<meta property="og:description"', content)
        self.assertIn('<meta property="og:type"', content)
        self.assertIn('<meta property="og:image"', content)

        # Twitter Cards
        self.assertIn('<meta name="twitter:card"', content)
        self.assertIn('<meta name="twitter:title"', content)
        self.assertIn('<meta name="twitter:description"', content)
        self.assertIn('<meta name="twitter:image"', content)

    def test_anime_detail_template_seo_tags(self):
        with open('templates/anime.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Title
        self.assertIn('<title>{{ (anime_info.title or anime_name) if anime_info else anime_name }} - AniBase</title>', content)

        # Meta description
        self.assertIn('<meta name="description" content="{{ (anime_info.description | striptags', content)
        self.assertIn('Watch {{ anime_name }} on AniBase', content)

        # Open Graph
        self.assertIn('<meta property="og:site_name" content="AniBase">', content)
        self.assertIn('<meta property="og:type" content="{{ \'video.movie\' if is_movie else \'video.tv_show\' }}">', content)
        self.assertIn('<meta property="og:image"', content)

        # Twitter Card
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', content)

        # H1 tag exists
        self.assertIn('<h1>{{ anime_info.title or anime_name if anime_info else anime_name }}</h1>', content)

    def test_player_template_seo_tags(self):
        with open('templates/player.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('<meta name="description" content="Streaming', content)
        self.assertIn('<meta property="og:site_name" content="AniBase">', content)
        self.assertIn('<meta property="og:type" content="{{ \'video.movie\' if is_movie else \'video.episode\' }}">', content)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', content)

    def test_child_templates_define_meta_descriptions(self):
        templates = [
            'templates/index.html',
            'templates/movies.html',
            'templates/schedule.html',
            'templates/seiyuu.html',
            'templates/studio.html',
            'templates/external_anime.html',
            'templates/project-overview.html',
            'templates/mal_profile.html',
            'templates/settings.html',
            'templates/setup.html',
        ]
        for template_path in templates:
            with open(template_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn('{% block meta_description %}', content, f"Missing meta_description block in {template_path}")

    def test_key_pages_have_h1_headings(self):
        pages_with_h1 = [
            'templates/index.html',
            'templates/movies.html',
            'templates/schedule.html',
            'templates/studio.html',
            'templates/project-overview.html',
            'templates/settings.html',
            'templates/seiyuu.html',
        ]
        for path in pages_with_h1:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn('<h1', content, f"Missing <h1> heading tag in {path}")


if __name__ == '__main__':
    unittest.main()
