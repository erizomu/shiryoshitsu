"""架空データだけでCLIの部分失敗・履歴出力を検証する。"""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shiryoshitsu import cli
from shiryoshitsu.client import FetchError
from shiryoshitsu.models import Bibliography
from shiryoshitsu.storage import Library
from test_providers import JSTAGE, NDL


class CLIRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "library.db"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = cli.main(["--db", str(self.db), *args])
        return status, out.getvalue(), err.getvalue()

    def test_partial_failure_continues_and_redacts_durable_log(self):
        log = self.root / "failures.jsonl"
        with patch.dict("os.environ", {"CINII_APP_ID": "secret/+value"}), patch(
            "shiryoshitsu.cli.get_xml",
            side_effect=[FetchError("appid=secret%2F%2Bvalue&retry=1 secret/+value"), NDL],
        ) as fetch:
            status, _, error = self.run_cli("collect", "--query", "史料", "--source", "jstage,ndl",
                                          "--delay", "0", "--log-file", str(log))
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 2)
        event = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(event["provider"], "jstage")
        self.assertEqual(event["query"], "史料")
        self.assertNotIn("secret", error + event["message"])
        with Library(self.db) as library:
            self.assertEqual(library.stats()["items"], 1)

    def test_all_failed_and_unconfigured_cinii_exit_one(self):
        with patch("shiryoshitsu.cli.get_xml", side_effect=FetchError("接続失敗")):
            self.assertEqual(self.run_cli("collect", "--query", "史料", "--source", "ndl", "--log-file", "")[0], 1)
        with patch.dict("os.environ", {"CINII_APP_ID": ""}), patch("shiryoshitsu.cli.get_xml") as fetch:
            self.assertEqual(self.run_cli("collect", "--query", "史料", "--source", "cinii", "--log-file", "")[0], 1)
            fetch.assert_not_called()

    def test_log_failure_does_not_stop_other_providers(self):
        with patch("shiryoshitsu.cli.get_xml", side_effect=[FetchError("接続失敗"), NDL]) as fetch:
            status, _, error = self.run_cli("collect", "--query", "史料", "--source", "jstage,ndl",
                                          "--delay", "0", "--log-file", str(self.root))
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("標準エラー出力のみで継続", error)

    def test_bad_options_rejected_without_network(self):
        with patch("shiryoshitsu.cli.get_xml") as fetch:
            for option, value in (("--delay", "nan"), ("--delay", "inf"), ("--timeout", "nan"),
                                  ("--timeout", "0"), ("--limit", "0"), ("--limit", "101")):
                with self.subTest(option=option, value=value):
                    self.assertEqual(self.run_cli("collect", "--query", "史料", option, value, "--log-file", "")[0], 1)
            self.assertEqual(self.run_cli("collect", "--query", " ", "--log-file", "")[0], 1)
            fetch.assert_not_called()

    def test_sync_validates_shape_and_deduplicates_tasks(self):
        config = self.root / "topics.json"
        for invalid in ([], {"topics": [1]}, {"topics": "x"}, {"topics": [{"name": "a", "queries": [3]}]}):
            config.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertEqual(self.run_cli("sync", "--config", str(config), "--log-file", "")[0], 1)
        config.write_text(json.dumps({"topics": [{"name": "史料", "queries": ["姜維", "姜維"]}]}), encoding="utf-8")
        with patch("shiryoshitsu.cli.get_xml", return_value=JSTAGE) as fetch:
            self.assertEqual(self.run_cli("sync", "--config", str(config), "--source", "jstage", "--log-file", "")[0], 0)
            self.assertEqual(fetch.call_count, 1)

    def test_export_keeps_provenance_and_guards_csv_formulas(self):
        with Library(self.db) as library:
            record = Bibliography("ndl", "test-1", "=架空タイトル", ("架空 著者",), 2020,
                                  url="https://example.test/item", source_institution="架空図書館")
            library.add(record, topic="人物", query="姜維")
            library.add(record, topic="史料", query="蜀漢")
        target = self.root / "out.csv"
        status, _, _ = self.run_cli("export", "--out", str(target))
        self.assertEqual(status, 0)
        with target.open(encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["title"], "'=架空タイトル")
        source, = json.loads(row["sources"])
        self.assertEqual(source["provider_id"], "test-1")
        self.assertEqual(source["source_institution"], "架空図書館")
        self.assertEqual(set(source["queries"]), {"姜維", "蜀漢"})
        self.assertEqual(len(source["observations"]), 2)
        target = self.root / "out.json"
        self.assertEqual(self.run_cli("export", "--format", "json", "--out", str(target))[0], 0)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["title"], "=架空タイトル")

    def test_output_cannot_overwrite_database_or_sidecars(self):
        self.run_cli("init")
        original = self.db.read_bytes()
        for suffix in ("", "-wal", "-shm"):
            self.assertEqual(self.run_cli("export", "--out", str(self.db) + suffix)[0], 1)
            self.assertEqual(self.run_cli("collect", "--query", "史料", "--log-file", str(self.db) + suffix)[0], 1)
        self.assertEqual(self.db.read_bytes(), original)

    def test_sqlite_failure_is_readable(self):
        self.db.write_text("データベースではありません", encoding="utf-8")
        status, _, error = self.run_cli("stats")
        self.assertEqual(status, 1)
        self.assertIn("エラー:", error)
        self.assertNotIn("Traceback", error)


if __name__ == "__main__":
    unittest.main()
