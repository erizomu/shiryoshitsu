import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from shiryoshitsu.models import Bibliography
from shiryoshitsu.storage import Library
from shiryoshitsu import cli
from shiryoshitsu.webui import render
from test_providers import JSTAGE, CINII, NDL


def item(provider="a", key="1", **kwargs):
    defaults = dict(title="鄧艾の政策研究", authors=("研究 太郎",), year=2024,
                    doi="10.1234/example.1", url="https://example.test/paper")
    defaults.update(kwargs)
    return Bibliography(provider=provider, provider_id=key, **defaults)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.temp.name) / "dir" / "literature.db"
        self.lib = Library(self.dbpath)

    def tearDown(self):
        self.lib.close()
        self.temp.cleanup()

    def test_doi_deduplicates_across_providers(self):
        self.assertEqual(self.lib.add(item(), topic="魏晋", query="鄧艾"), (True, True))
        self.assertEqual(self.lib.add(item("b", "2", doi="https://doi.org/10.1234/EXAMPLE.1")), (False, True))
        self.assertEqual(self.lib.add(item(), topic="魏晋", query="鄧艾"), (False, False))
        self.assertEqual(self.lib.stats()["items"], 1)
        self.assertEqual(self.lib.stats()["sources"], 2)
        self.assertEqual(len(self.lib.search("鄧艾")), 1)
        self.assertEqual(self.lib.search("政策")[0]["topics"], ["魏晋"])

    def test_fallback_uses_title_year_first_author(self):
        self.lib.add(item(doi=None, url="https://first.example"))
        self.lib.add(item("b", "2", doi=None, url="https://second.example", title="鄧 艾 の政策研究"))
        self.assertEqual(self.lib.stats()["items"], 1)
        self.lib.add(item("c", "3", doi=None, authors=(), url="https://third.example"))
        self.assertEqual(self.lib.stats()["items"], 2)

    def test_distinct_dois_never_merge_from_title(self):
        self.lib.add(item())
        self.lib.add(item("b", "2", doi="10.9876/different"))
        self.assertEqual(self.lib.stats()["items"], 2)

    def test_new_doi_later_and_merges_records(self):
        self.lib.add(item("a", "1", doi=None))
        self.lib.add(item("b", "2", doi="10.1234/example.1", authors=("別名の著者",)))
        self.assertEqual(self.lib.stats()["items"], 2)
        self.lib.add(item("a", "1", doi="10.1234/example.1"))
        self.assertEqual(self.lib.stats()["items"], 1)
        self.assertEqual(self.lib.stats()["sources"], 2)

    def test_search_filters_and_literal_wildcards(self):
        self.lib.add(item(title="姜維_研究"), topic="蜀漢")
        self.assertEqual(len(self.lib.search("姜維_")), 1)
        self.assertEqual(len(self.lib.search("姜維%")), 0)
        self.assertEqual(len(self.lib.search("姜維", topic="山西")), 0)
        self.assertEqual(len(self.lib.search("姜維", provider="a")), 1)
        self.assertEqual(len(self.lib.search("姜維", from_year=2025)), 0)

    def test_html_escapes_untrusted_titles(self):
        self.lib.add(item(title="<script>alert(1)</script>", doi=None, authors=()))
        page = render(self.lib)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_two_topics_for_same_record(self):
        self.lib.add(item(), topic="人物")
        self.lib.add(item(), topic="魏晋")
        self.assertEqual(self.lib.stats()["items"], 1)
        self.assertEqual(self.lib.stats()["topics"], 2)

    def test_export_csv_and_json(self):
        self.lib.add(item(), topic="魏晋")
        out = Path(self.temp.name) / "data.csv"
        with contextlib.redirect_stdout(io.StringIO()) as csv_message:
            cli._export(self.lib.search(), "csv", str(out))
        self.assertIn("書き出しました", csv_message.getvalue())
        self.assertTrue(out.read_bytes().startswith(b"\xef\xbb\xbf"))
        out2 = Path(self.temp.name) / "data.json"
        with contextlib.redirect_stdout(io.StringIO()) as json_message:
            cli._export(self.lib.search(), "json", str(out2))
        self.assertIn("書き出しました", json_message.getvalue())
        self.assertEqual(json.loads(out2.read_text(encoding="utf-8"))[0]["title"], "鄧艾の政策研究")


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "cli.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_init_and_search(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--db", self.db, "init"]), 0)
            self.assertEqual(cli.main(["--db", self.db, "search", "姜維"]), 0)
        self.assertTrue(Path(self.db).exists())

    def test_end_to_end_mocked_apis(self):
        def fake_fetch(url, *, timeout, minimum_delay):
            if "jstage" in url:
                return JSTAGE
            if "ndlsearch" in url:
                return NDL
            return CINII
        with patch("shiryoshitsu.cli.get_xml", side_effect=fake_fetch):
            with patch.dict("os.environ", {"CINII_APP_ID": "dummy"}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--db", self.db, "collect", "--query", "鄧艾", "--topic", "魏晋", "--delay", "0"]), 0)
                    self.assertEqual(cli.main(["--db", self.db, "collect", "--query", "鄧艾", "--topic", "魏晋", "--delay", "0"]), 0)
        with Library(self.db) as library:
            self.assertEqual(library.stats()["items"], 2)  # 同一DOIのJ-STAGE・CiNiiと、別書誌のNDL
            self.assertEqual(library.stats()["sources"], 3)
            self.assertEqual(len(library.search("鄧艾")), 1)

    def test_cinii_missing_skips_without_leaking_key(self):
        with patch("shiryoshitsu.cli.get_xml", return_value=JSTAGE):
            with patch.dict("os.environ", {"CINII_APP_ID": ""}):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    status = cli.main(["--db", self.db, "collect", "--query", "鄧艾", "--source", "jstage,cinii", "--delay", "0"])
        self.assertEqual(status, 2)
        self.assertIn("SKIP", err.getvalue())
