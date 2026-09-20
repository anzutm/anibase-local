import unittest
import os
from pathlib import Path
from unittest.mock import patch

import main
from tests import test_watch_history as history_tests


class DiscordPresenceTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            'RPC_DESIRED': None, 'RPC_SESSION_VERSIONS': {},
            'DISCORD_RPC_ENABLED': True, 'DISCORD_CLIENT_ID': 'test',
            'DISCORD_TIMER_MODE': 'remaining', 'RPC_CONNECTION_STATUS': 'idle',
            'Presence': history_tests.DiscordRpcLifecycleTests.FakePresence,
            'rpc': None, 'rpc_connected': False, 'CURRENT_RPC_OWNER': None,
            'CURRENT_RPC_ANIME': None, 'RPC_START_TIME': None,
            'RPC_LAST_PAYLOAD': None, 'RPC_LAST_SENT_AT': 0, 'RPC_RETRY_AT': 0, 'RPC_RETRY_DELAY': 2,
        }.items():
            p = patch.object(main, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name in ('dispatch_discord_rpc_task', 'start_discord_presence_monitor'):
            p = patch.object(main, name)
            p.start()
            self.addCleanup(p.stop)

    def event(self, session='a', sequence=1, event='playing', **extra):
        return main.accept_discord_presence_event(dict(
            session=session, sequence=sequence, event=event,
            title='Demo', label='Season 1 · Episode 2',
            position=120, duration=1440, speed=2, **extra))

    def test_late_play_cannot_restore_paused_session(self):
        self.event()
        self.event(sequence=3, event='pause')
        self.assertFalse(self.event(sequence=2))
        self.assertTrue(main.RPC_DESIRED['paused'])
        self.assertNotIn('start', main.RPC_DESIRED['payload'])
        self.assertNotIn('end', main.RPC_DESIRED['payload'])

    def test_old_tab_heartbeat_and_stop_cannot_steal_owner(self):
        self.event()
        self.event(session='b')
        self.assertFalse(self.event(sequence=2, event='heartbeat'))
        self.assertFalse(self.event(sequence=3, event='stop'))
        self.assertEqual(main.RPC_DESIRED['session'], 'b')

    def test_heartbeat_recovers_after_lost_initial_request(self):
        self.assertTrue(self.event(sequence=2, event='heartbeat'))
        self.assertEqual(main.RPC_DESIRED['session'], 'a')

    def test_poster_brand_icon_and_anilist_button(self):
        metadata = {'poster': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/test.jpg',
                    'anilist_id': 154587, 'mal_id': 52991}
        with patch.object(main, 'discord_media_metadata', return_value=metadata):
            art = main.discord_media_artwork('Frieren', 'Season 1/08.mkv', 'Frieren')
        self.assertEqual(art['large_image'], metadata['poster'])
        self.assertEqual(art['small_image'], 'anibase_logo')
        self.assertEqual(art['large_text'], 'Frieren')
        self.assertEqual(art['buttons'], [{'label': 'View on AniList',
                                         'url': 'https://anilist.co/anime/154587'}])

    def test_missing_or_private_poster_uses_logo(self):
        for poster in (None, '/poster/Frieren', 'http://127.0.0.1/poster.jpg',
                       'https://s4.anilist.co.evil.test/image.jpg', 'https://s4.anilist.co:bad/img'):
            with self.subTest(poster=poster), patch.object(main, 'discord_media_metadata',
                                                         return_value={'poster': poster}):
                art = main.discord_media_artwork('Demo', '01.mkv', 'Demo')
                self.assertEqual(art['large_image'], 'anibase_logo')
                self.assertNotIn('small_image', art)
                self.assertNotIn('buttons', art)

    def test_movie_uses_mal_link_when_anilist_id_is_absent(self):
        metadata = {'title': 'Summer Ghost', 'mal_id': 48171,
                    'poster': 'https://cdn.myanimelist.net/images/anime/1/test.jpg'}
        with patch.object(main, 'discord_media_metadata', return_value=metadata):
            self.event(anime_name='Movies', episode='Summer Ghost.mkv')
        payload = main.RPC_DESIRED['payload']
        self.assertEqual(payload['details'], 'Summer Ghost')
        self.assertEqual(payload['state'], 'Movie · Watching')
        self.assertEqual(payload['buttons'][0]['url'], 'https://myanimelist.net/anime/48171')

    def test_new_artwork_on_heartbeat_keeps_timer(self):
        with patch.object(main, 'discord_media_metadata', return_value={}), \
             patch.object(main.time, 'time', return_value=1000):
            self.event(anime_name='Demo', episode='01.mkv')
        with patch.object(main, 'discord_media_metadata', return_value={'anilist_id': 12}), \
             patch.object(main.time, 'time', return_value=1001):
            self.event(sequence=2, event='heartbeat', anime_name='Demo', episode='01.mkv')
        self.assertEqual(main.RPC_DESIRED['payload']['start'], 940)
        self.assertIn('buttons', main.RPC_DESIRED['payload'])

    def test_timestamps_follow_position_and_speed(self):
        with patch.object(main.time, 'time', return_value=1000):
            self.event()
        self.assertEqual(main.RPC_DESIRED['payload']['start'], 940)
        self.assertEqual(main.RPC_DESIRED['payload']['end'], 1660)

    def test_identical_payload_is_not_published_twice(self):
        self.event()
        main.reconcile_discord_presence()
        main.reconcile_discord_presence()
        self.assertEqual(len(main.rpc.updates), 1)
        self.assertEqual(main.rpc.updates[0]['activity_type'], main.ActivityType.WATCHING)
        self.assertEqual(main.rpc.updates[0]['status_display_type'], main.StatusDisplayType.DETAILS)
        self.event(sequence=2, event='pause')
        main.reconcile_discord_presence()
        self.assertEqual(main.rpc.clear_count, 0)
        self.assertTrue(main.rpc.updates[-1]['state'].startswith('Paused'))
        self.event(sequence=3, event='stop')
        main.reconcile_discord_presence()
        self.assertEqual(main.rpc.clear_count, 1)

    def test_paused_heartbeat_renews_lease_without_timer_and_resume_restores_it(self):
        self.event()
        self.event(sequence=2, event='pause')
        main.RPC_DESIRED['expires'] = 0
        self.event(sequence=3, event='heartbeat')
        self.assertGreater(main.RPC_DESIRED['expires'], main.time.monotonic())
        self.assertNotIn('start', main.RPC_DESIRED['payload'])
        self.event(sequence=4, event='playing')
        self.assertIn('end', main.RPC_DESIRED['payload'])
        self.assertFalse(main.RPC_DESIRED.get('paused'))

    def test_switch_timer_to_elapsed_removes_countdown_on_heartbeat(self):
        self.event()
        with patch.object(main, 'DISCORD_TIMER_MODE', 'elapsed'):
            self.event(sequence=2, event='heartbeat')
        self.assertIn('start', main.RPC_DESIRED['payload'])
        self.assertNotIn('end', main.RPC_DESIRED['payload'])

    def test_external_presence_uses_watching_and_anime_title(self):
        with patch.object(main, 'discord_media_description', return_value=('Film', 'Movie')):
            main.start_external_discord_presence('Movies', 'film.mkv', 'external')
        main.reconcile_discord_presence()
        self.assertEqual(main.rpc.updates[-1]['details'], 'Film')
        self.assertEqual(main.rpc.updates[-1]['status_display_type'], main.StatusDisplayType.DETAILS)

    def test_season_and_custom_episode_title_are_normalized(self):
        with patch.object(main, 'discord_media_metadata', return_value={'title': 'Demo'}), \
             patch.object(main, 'get_episode_display_override', return_value='The Journey'):
            title, label = main.discord_media_description('Demo', 'S02/04.mkv')
        self.assertEqual(title, 'Demo')
        self.assertEqual(label, 'Season 2 · Episode 4 · The Journey')

    def test_connection_failure_uses_backoff_and_recovers(self):
        self.event()
        with patch.object(main.Presence, 'connect', side_effect=OSError('not running')):
            main.reconcile_discord_presence()
        self.assertFalse(main.rpc_connected)
        self.assertGreater(main.RPC_RETRY_AT, main.time.monotonic())
        with patch.object(main, 'Presence') as factory:
            main.reconcile_discord_presence()
            factory.assert_not_called()
        main.RPC_RETRY_AT = 0
        main.reconcile_discord_presence()
        self.assertTrue(main.rpc_connected)

    def test_settings_distinguish_connection_states(self):
        settings = {'discord_rpc_enabled': True, 'discord_client_id': '123456789012345678'}
        diagnostics = {'vlc': {'status': 'available', 'message': 'Available'}}
        for connection, expected in [('idle', 'Ready'), ('unavailable', 'Discord not available'),
                                     ('retrying', 'Reconnecting'), ('invalid_id', 'Invalid Client ID')]:
            with self.subTest(connection=connection), patch.object(main, 'RPC_CONNECTION_STATUS', connection):
                cards = main.build_settings_status_cards(settings, diagnostics)
                self.assertEqual(next(c for c in cards if c['label'] == 'Discord')['text'], expected)

    def test_invalid_timing_is_rejected(self):
        with self.assertRaises(ValueError):
            main.accept_discord_presence_event(dict(session='a', sequence=1,
                event='playing', position=float('nan')))

    def test_lan_presence_is_ignored(self):
        with main.app.test_request_context('/api/discord/presence', method='POST',
                                          environ_base={'REMOTE_ADDR': '192.168.1.20'}):
            response = main.discord_presence_event.__wrapped__()
            self.assertTrue(response.get_json()['ignored'])
            self.assertIsNone(main.RPC_DESIRED)

    def test_external_launcher_does_not_clear_browser_owner(self):
        self.event()
        process = unittest.mock.Mock()
        main.clear_discord_rpc_when_process_exits(process, 'external-old')
        self.assertEqual(main.RPC_DESIRED['session'], 'a')

    def test_external_handoff_has_bounded_presence(self):
        with patch.object(main, 'discord_media_description', return_value=('Film', 'Movie')):
            main.start_external_discord_presence('Movies', 'film.mkv', 'external')
        main.clear_discord_rpc_when_process_exits(unittest.mock.Mock(), 'external')
        self.assertIn('expires', main.RPC_DESIRED)
        self.assertEqual(main.RPC_DESIRED['payload']['state'], 'Opened in external player')

    def test_expired_browser_presence_is_removed(self):
        self.event()
        main.RPC_DESIRED['expires'] = 0
        with patch.object(main, 'wait_for_shutdown', side_effect=[False, True]), \
             patch.object(main, 'reset_discord_rpc'):
            main.monitor_discord_presence()
        self.assertIsNone(main.RPC_DESIRED)

    def test_unchanged_presence_checks_connection_periodically(self):
        self.event()
        main.reconcile_discord_presence()
        main.RPC_LAST_SENT_AT -= 31
        main.reconcile_discord_presence()
        self.assertEqual(len(main.rpc.updates), 2)


class DiscordPresenceBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ANIBASE_BROWSER_TESTS') == '1', 'Opt-in browser test')
    def test_only_playback_publishes_and_lifecycle_is_ordered(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p, p.chromium.launch(channel='msedge', headless=True) as browser:
            page = browser.new_page()
            page.set_content('<video id="video"></video>')
            page.evaluate('''() => {
                window.sent = [];
                window.setInterval = callback => { window.presenceHeartbeat = callback; return 1; };
                window.fetch = async (url, options) => {
                    sent.push({...JSON.parse(options.body), keepalive: options.keepalive});
                    return {ok:true};
                };
            }''')
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'static/discord-presence.js'))
            page.evaluate('''() => {
                const video = document.querySelector('video');
                new AniBaseDiscordPresence(video, () => ({title:'Film', label:'Movie'}));
                video.dispatchEvent(new Event('canplay'));
            }''')
            self.assertEqual(page.evaluate('sent.length'), 0)
            page.evaluate('''() => {
                const video = document.querySelector('video');
                for (const event of ['playing', 'pause', 'playing', 'waiting', 'playing']) {
                    video.dispatchEvent(new Event(event));
                    if (event === 'pause') window.presenceHeartbeat();
                }
                window.dispatchEvent(new PageTransitionEvent('pagehide'));
            }''')
            events = page.evaluate('sent')
            self.assertEqual([x['event'] for x in events],
                             ['playing', 'pause', 'heartbeat', 'playing', 'waiting', 'playing', 'stop'])
            self.assertEqual([x['sequence'] for x in events], list(range(1, 8)))
            self.assertEqual(len({x['session'] for x in events}), 1)
            self.assertTrue(events[-1]['keepalive'])


if __name__ == '__main__':
    unittest.main()
