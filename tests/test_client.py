import io
import unittest
from urllib.error import HTTPError, URLError
from http.client import IncompleteRead, BadStatusLine
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from unittest.mock import patch
from shiryoshitsu.client import FetchError, get_xml


class FakeReply:
    def __init__(self, data):
        self.data = io.BytesIO(data)
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, size):
        return self.data.read(size)


class ClientTests(unittest.TestCase):
    def test_fetch(self):
        with patch("shiryoshitsu.client.urlopen", return_value=FakeReply(b"<feed/>")) as request:
            self.assertEqual(get_xml("https://example.test/api"), b"<feed/>")
            self.assertIn("Shiryoshitsu", request.call_args.args[0].get_header("User-agent"))
    def test_size_limit(self):
        with patch("shiryoshitsu.client.urlopen", return_value=FakeReply(b"X" * (6_000_001))):
            with self.assertRaises(FetchError):
                get_xml("https://example.test/api")

    def test_retry_after_is_respected(self):
        failure = HTTPError("https://example.test", 429, "busy", {"Retry-After": "5"}, None)
        with patch("shiryoshitsu.client.urlopen", side_effect=[failure, FakeReply(b"<rss/>")]) as fetch, patch("shiryoshitsu.client.time.sleep") as sleep:
            self.assertEqual(get_xml("https://example.test"), b"<rss/>")
            self.assertEqual(fetch.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_long_retry_after_does_not_retry_early(self):
        failure = HTTPError("https://example.test", 503, "busy", {"Retry-After": "3600"}, None)
        with patch("shiryoshitsu.client.urlopen", side_effect=failure) as fetch, patch("shiryoshitsu.client.time.sleep") as sleep:
            with self.assertRaisesRegex(FetchError, "待機"):
                get_xml("https://example.test")
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()

    def test_permanent_http_error_not_retried_or_leaked(self):
        failure = HTTPError("https://example.test?appid=secret", 403, "secret", {}, None)
        with patch("shiryoshitsu.client.urlopen", side_effect=failure) as fetch:
            with self.assertRaises(FetchError) as error:
                get_xml("https://example.test?appid=secret")
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(fetch.call_count, 1)

    def test_network_retries_are_bounded(self):
        with patch("shiryoshitsu.client.urlopen", side_effect=URLError("secret")) as fetch, patch("shiryoshitsu.client.time.sleep"):
            with self.assertRaises(FetchError) as error:
                get_xml("https://example.test", retries=2)
            self.assertEqual(fetch.call_count, 3)
            self.assertNotIn("secret", str(error.exception))

    def test_invalid_timeout(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                get_xml("https://example.test", timeout=timeout)

    def test_broken_http_response_is_a_bounded_fetch_error(self):
        for error in (IncompleteRead(b"part"), BadStatusLine("invalid")):
            with self.subTest(error=type(error).__name__), patch("shiryoshitsu.client.urlopen", side_effect=error) as fetch, patch("shiryoshitsu.client.time.sleep") as sleep:
                with self.assertRaises(FetchError):
                    get_xml("https://example.test", minimum_delay=10)
                self.assertEqual(fetch.call_count, 3)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [10, 10])

    def test_retry_after_http_date(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
        failure = HTTPError("https://example.test", 503, "busy", {"Retry-After": future}, None)
        with patch("shiryoshitsu.client.urlopen", side_effect=[failure, FakeReply(b"ok")]), patch("shiryoshitsu.client.time.sleep") as sleep:
            self.assertEqual(get_xml("https://example.test"), b"ok")
            self.assertGreater(sleep.call_args.args[0], 18)
            self.assertLessEqual(sleep.call_args.args[0], 20)
