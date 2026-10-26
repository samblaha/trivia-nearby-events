import copy
import http.client
import io
import json
import threading
import unittest
from unittest.mock import patch

import business_events
import events_api


class PublicFeedAdapterTests(unittest.TestCase):
    def test_read_uses_public_projection_and_normalizes_columbus(self):
        for location in ('Columbus', 'Columbus, Ohio', '43215'):
            with patch('business_events.urllib.request.urlopen', return_value=io.BytesIO(b'[]')) as request:
                self.assertEqual(business_events.search_business_events(location), [])
                sent = request.call_args.args[0]
                self.assertTrue(sent.full_url.endswith('/rest/v1/rpc/business_event_feed'))
                self.assertEqual(json.loads(sent.data), {'search_city': 'Columbus, OH'})
                self.assertEqual(sent.get_header('Apikey'), business_events.PUBLIC_KEY)
                self.assertIsNone(sent.get_header('Authorization'))
                self.assertEqual(request.call_args.kwargs['timeout'], 4)

    def test_timeout_and_invalid_response_do_not_break_search(self):
        with patch('business_events.urllib.request.urlopen', side_effect=TimeoutError):
            self.assertEqual(business_events.search_business_events('Columbus, OH'), [])
        with patch('business_events.urllib.request.urlopen', return_value=io.BytesIO(b'not json')):
            self.assertEqual(business_events.search_business_events('Columbus, OH'), [])


class BusinessDeliveryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = events_api.ThreadingHTTPServer(('127.0.0.1', 0), events_api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        events_api._search_cache.clear()
        self.google = {'source': 'serpapi_google_events', 'events': [{'id': 'google', 'title': 'Other trivia'}], 'count': 1}
        self.night = {'id': 'business-night-2026-11-05', 'series_id': 'business-night', 'title': 'Host trivia', 'venue_name': 'Pub', 'start_date': '2026-11-05T19:00:00-05:00', 'sponsored': False}

    def get(self, path='/events?location=Columbus%2C%20OH&when=week'):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            client.request('GET', path)
            response = client.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            client.close()

    def test_published_then_paused_event_disappears_even_on_google_cache_hit(self):
        events_api._search_cache_set('Columbus, OH', 'week', self.google)
        with patch('events_api.search_business_events', side_effect=[[self.night], []]) as managed, patch('events_api.search_events') as google:
            first = self.get()
            second = self.get()
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]['Cache-Control'], 'no-store')
        self.assertEqual([e['id'] for e in first[2]['events']], ['google', self.night['id']])
        self.assertEqual(second[2]['events'], self.google['events'])
        self.assertEqual(second[2]['count'], 1)
        self.assertEqual(events_api._search_cache_get('Columbus, OH', 'week'), self.google)
        self.assertEqual(managed.call_count, 2)
        google.assert_not_called()

    def test_fresh_google_results_include_managed_nights(self):
        with patch('events_api.search_business_events', return_value=[self.night]), patch('events_api.search_events', return_value=copy.deepcopy(self.google)):
            status, _, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body['count'], 2)
        self.assertEqual(body['events'][-1]['series_id'], 'business-night')

    def test_google_failure_still_serves_managed_nights(self):
        with patch('events_api.search_business_events', return_value=[self.night]), patch('events_api.search_events', side_effect=RuntimeError('Upstream unavailable')):
            status, _, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body['events'], [self.night])

    def test_no_sources_retains_error_instead_of_false_empty_success(self):
        with patch('events_api.search_business_events', return_value=[]), patch('events_api.search_events', side_effect=RuntimeError('Upstream unavailable')):
            self.assertEqual(self.get()[0], 502)

    def test_blank_location_is_rejected_before_provider_requests(self):
        with patch('events_api.search_business_events') as managed, patch('events_api.search_events') as google:
            self.assertEqual(self.get('/events?location=%20')[0], 400)
        managed.assert_not_called()
        google.assert_not_called()

    def test_details_requests_do_not_fetch_managed_feed(self):
        with patch('events_api.search_business_events') as managed, patch('events_api.event_details', return_value={'title': 'Trivia'}):
            self.assertEqual(self.get('/events?token=test')[2]['title'], 'Trivia')
        managed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
