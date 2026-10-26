import io
import unittest
from unittest.mock import patch

import events_api


class SearchDeadlineTests(unittest.TestCase):
    def test_expired_budget_never_starts_another_provider_request(self):
        with patch('events_api.time.monotonic', return_value=100), patch('events_api.urllib.request.urlopen') as network:
            with self.assertRaises(TimeoutError):
                events_api._http_get({'q': 'trivia'}, deadline=99)
        network.assert_not_called()

    def test_socket_timeout_is_limited_to_remaining_budget(self):
        with patch('events_api.time.monotonic', return_value=100), patch('events_api.urllib.request.urlopen', return_value=io.BytesIO(b'{}')) as network:
            events_api._http_get({'q': 'trivia'}, deadline=102.5)
        self.assertEqual(network.call_args.kwargs['timeout'], 2.5)

    def test_total_provider_failure_is_not_cached_as_no_events(self):
        events_api._search_cache.clear()
        with patch('events_api._api_key', return_value='test'), patch('events_api._http_get', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, 'temporarily unavailable'):
                events_api.search_events('Columbus, OH')
        self.assertIsNone(events_api._search_cache_get('Columbus, OH', 'week'))

    def test_slow_first_query_stops_retries_and_extra_locations(self):
        clock = [100.0]
        calls = []
        def slow_request(params, **options):
            calls.append(params)
            clock[0] = options['deadline']
            raise TimeoutError()
        with patch('events_api._api_key', return_value='test'), patch('events_api.time.monotonic', side_effect=lambda: clock[0]), patch('events_api._http_get', side_effect=slow_request):
            with self.assertRaises(RuntimeError):
                events_api.search_events('Dublin, OH', use_cache=False)
        self.assertEqual(len(calls), 1)

    def test_successful_empty_search_is_cached_as_zero_results(self):
        payload = {
            'search_metadata': {'status': 'Success'},
            'error': "Google hasn't returned any results for this query.",
        }
        events_api._search_cache.clear()
        with patch('events_api._api_key', return_value='test'), patch('events_api._http_get', return_value=payload) as network:
            result = events_api.search_events('Empty test city')
            self.assertEqual(result['count'], 0)
            self.assertEqual(result['events'], [])
            self.assertTrue(any('htichips' not in call.args[0] for call in network.call_args_list))
            network.reset_mock()
            self.assertEqual(events_api.search_events('Empty test city'), result)
            network.assert_not_called()

    def test_empty_error_without_success_status_is_not_cached(self):
        payload = {
            'search_metadata': {'status': 'Error'},
            'error': "Google hasn't returned any results for this query.",
        }
        events_api._search_cache.clear()
        with patch('events_api._api_key', return_value='test'), patch('events_api._http_get', return_value=payload):
            with self.assertRaisesRegex(RuntimeError, 'temporarily unavailable'):
                events_api.search_events('Failed test city')
        self.assertIsNone(events_api._search_cache_get('Failed test city', 'week'))


if __name__ == '__main__':
    unittest.main()
