"""Web画面をソケット・ネットワーク通信なしで検証する。"""
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shiryoshitsu.models import Bibliography
from shiryoshitsu.storage import Library
from shiryoshitsu.webui import make_handler, render, serve


class MemoryConnection:
    """BaseHTTPRequestHandler用のメモリ内接続。"""
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *_args):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Library(Path(self.temp.name) / "test.db")
        self.library.add(Bibliography("test", "1", "姜維の研究", authors=("架空 著者",), year=2024))

    def tearDown(self):
        self.library.close()
        self.temp.cleanup()

    def request(self, path="/", *, headers=("Host: 127.0.0.1:8765",), method="GET"):
        message = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
        connection = MemoryConnection(message.encode("ascii"))
        with patch("socket.socket", side_effect=AssertionError("ネットワーク通信は禁止")):
            make_handler(self.library)(connection, ("127.0.0.1", 12345), SimpleNamespace(server_port=8765))
        raw_headers, body = bytes(connection.response).split(b"\r\n\r\n", 1)
        return raw_headers.decode("ascii"), body.decode("utf-8")

    def test_japanese_search_and_response_headers(self):
        headers, page = self.request("/?q=%E5%A7%9C%E7%B6%AD")
        self.assertIn("200 OK", headers)
        self.assertIn("Content-Type: text/html; charset=utf-8", headers)
        self.assertIn("Cache-Control: no-store", headers)
        self.assertIn("frame-ancestors 'none'", headers)
        self.assertIn("姜維の研究", page)
        self.assertIn("NDLサーチAPIを使用", page)
        _, empty = self.request("/?q=%E7%9F%B3%E8%8B%9E")
        self.assertIn("一致する書誌はありません", empty)

    def test_escape_query_and_legacy_data(self):
        self.library.db.execute(
            "UPDATE items SET title=?, url=?, authors_json=?, publication=?, doi=?",
            ('<script>bad()</script>', 'javascript:alert(1)', '["<img src=x>"]', '<iframe>', '<svg>'),
        )
        page = render(self.library)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertIn("&lt;iframe&gt;", page)
        self.assertIn("&lt;svg&gt;", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn("<script>", page)
        query_page = render(self.library, '\"><script>bad()</script>')
        self.assertIn("&quot;&gt;&lt;script&gt;", query_page)
        self.assertNotIn("<script>", query_page)

    def test_reject_nonlocal_missing_duplicate_and_wrong_port_host(self):
        for hosts in ((), ("Host: evil.example:8765",), ("Host: 127.0.0.1:9999",),
                      ("Host: 127.0.0.1:8765", "Host: evil.example"),
                      ("Host: 127.0.0.1:8765@evil.example",)):
            with self.subTest(hosts=hosts):
                headers, page = self.request(headers=hosts)
                self.assertIn("403 Forbidden", headers)
                self.assertNotIn("姜維の研究", page)
        headers, _ = self.request(headers=("Host: localhost:8765",))
        self.assertIn("200 OK", headers)

    def test_not_found_bad_request_and_read_only(self):
        headers, page = self.request("/missing")
        self.assertIn("404 Not Found", headers)
        self.assertIn("ページが見つかりません", page)
        for path in ("http://evil.example/", "/?" + "&".join("q=x" for _ in range(21))):
            headers, _ = self.request(path)
            self.assertIn("400 Bad Request", headers)
        headers, page = self.request(method="POST")
        self.assertIn("405 Method Not Allowed", headers)
        self.assertIn("Allow: GET", headers)
        self.assertIn("閲覧専用", page)

    def test_public_bind_and_invalid_port_are_rejected_before_socket(self):
        with patch("shiryoshitsu.webui.HTTPServer") as server:
            for host in ("0.0.0.0", "::", "::1", "192.168.0.1", "evil.example"):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "ローカル専用"):
                    serve(self.library, host=host)
            for port in (0, -1, 65536):
                with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port"):
                    serve(self.library, port=port)
            server.assert_not_called()

    def test_localhost_binds_explicitly_to_loopback(self):
        with patch("shiryoshitsu.webui.HTTPServer") as server, contextlib.redirect_stdout(io.StringIO()):
            serve(self.library, host="localhost")
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8765))
        server.return_value.__enter__.return_value.serve_forever.assert_called_once()


if __name__ == "__main__":
    unittest.main()
