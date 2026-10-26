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


if __name__ == '__main__':
    unittest.main()
