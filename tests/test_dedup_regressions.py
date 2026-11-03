"""誤統合、再収集時の履歴消失、既存DB移行を検証する架空データ。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from shiryoshitsu.models import Bibliography, find_year, normalize_doi, safe_url
from shiryoshitsu.storage import Library


def record(provider="a", key="1", **overrides):
    values = dict(title="姜維と鄧艾", authors=("架空 太郎",), year=2024, doi=None,
                  url="https://example.test/1")
    values.update(overrides)
    return Bibliography(provider, key, **values)


class DedupRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "library.db"
        self.library = Library(self.path)

    def tearDown(self):
        self.library.close()
        self.temp.cleanup()

    def test_doi_less_record_does_not_choose_between_distinct_dois(self):
        self.library.add(record(doi="10.1234/one"))
        self.library.add(record("b", "2", doi="10.1234/two"))
        self.library.add(record("c", "3"))
        self.library.add(record("c", "3"))
        self.assertEqual(self.library.stats()["items"], 3)
        self.assertEqual({item["doi"] for item in self.library.search()},
                         {"10.1234/one", "10.1234/two", None})

    def test_doi_does_not_choose_ambiguous_doi_less_candidate(self):
        self.library.add(record(doi="10.1234/one"))
        self.library.add(record("b", "2", doi="10.1234/two"))
        self.library.add(record("c", "3"))
        self.library.add(record("d", "4", doi="10.1234/three"))
        self.assertEqual(self.library.stats()["items"], 4)

    def test_missing_doi_can_match_one_complete_candidate(self):
        self.library.add(record(doi="10.1234/one"))
        self.library.add(record("b", "2"))
        self.assertEqual(self.library.stats()["items"], 1)
        self.assertEqual(self.library.stats()["sources"], 2)

    def test_matching_doi_overrides_different_metadata(self):
        self.library.add(record(doi="10.1234/one"))
        self.library.add(record("b", "2", doi="https://doi.org/10.1234/ONE",
                                title="別言語のタイトル", authors=(), year=None))
        self.assertEqual(self.library.stats()["items"], 1)

    def test_conflicting_doi_on_same_source_is_rejected_atomically(self):
        self.library.add(record(doi="10.1234/one"), query="姜維", topic="人物")
        before = self.library.search()
        with self.assertRaisesRegex(ValueError, "異なるDOI"):
            self.library.add(record(doi="10.1234/two"), query="鄧艾", topic="軍事")
        self.assertEqual(self.library.search(), before)

    def test_known_source_metadata_change_cannot_merge_distinct_records(self):
        self.library.add(record(title="姜維の研究"))
        self.library.add(record("b", "2", title="鄧艾の研究"))
        self.library.add(record(title="鄧艾の研究"))
        self.assertEqual(self.library.stats()["items"], 2)
        self.assertEqual(len(self.library.search("姜維")), 1)

    def test_punctuation_keeps_distinct_titles_separate(self):
        self.library.add(record(title="A-Bの比較"))
        self.library.add(record("b", "2", title="ABの比較"))
        self.assertEqual(self.library.stats()["items"], 2)

    def test_missing_author_or_year_does_not_merge(self):
        for index, overrides in enumerate(({"authors": ()}, {"year": None})):
            self.library.add(record("a", str(index), **overrides))
            self.library.add(record("b", str(index), **overrides))
        self.assertEqual(self.library.stats()["items"], 4)

    def test_queries_topics_and_original_urls_survive_recollection(self):
        self.library.add(record(source_institution="架空大学"), query="姜維", topic="人物")
        self.library.add(record(url="https://example.test/2", source_institution="架空大学"),
                         query="鄧艾", topic="軍事")
        self.library.add(record(url="https://example.test/2", source_institution="架空大学"),
                         query="鄧艾", topic="軍事")
        result, = self.library.search()
        source, = result["sources"]
        self.assertEqual(set(source["queries"]), {"姜維", "鄧艾"})
        self.assertEqual(source["source_institution"], "架空大学")
        self.assertEqual({(row["search_query"], row["topic"], row["source_url"])
                          for row in source["observations"]},
                         {("姜維", "人物", "https://example.test/1"),
                          ("鄧艾", "軍事", "https://example.test/2")})

    def test_later_doi_merge_preserves_all_source_observations(self):
        self.library.add(record(), query="姜維", topic="人物")
        self.library.add(record("b", "2", doi="10.1234/one", title="別言語題名"),
                         query="Jiang Wei", topic="蜀漢")
        self.library.add(record(doi="10.1234/one"), query="鄧艾", topic="軍事")
        result, = self.library.search()
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(set(result["topics"]), {"人物", "蜀漢", "軍事"})
        observations = [observation for source in result["sources"] for observation in source["observations"]]
        self.assertEqual({item["search_query"] for item in observations}, {"姜維", "鄧艾", "Jiang Wei"})

    def test_existing_v1_database_migrates_without_losing_data(self):
        self.library.add(record(title="A-Bの比較"), query="姜維", topic="人物")
        # 旧版と同じカラム構成・照合キー・バージョンへ戻して移行を検証する。
        with self.library.db:
            self.library.db.execute("DROP TABLE observations")
            self.library.db.execute("ALTER TABLE appearances DROP COLUMN source_institution")
            self.library.db.execute("UPDATE items SET title_key='abの比較'")
            self.library.db.execute("PRAGMA user_version=1")
        self.library.close()
        self.library = Library(self.path)
        result, = self.library.search()
        self.assertEqual(result["title"], "A-Bの比較")
        self.assertEqual(result["topics"], ["人物"])
        source, = result["sources"]
        self.assertEqual(source["queries"], ["姜維"])
        self.assertEqual(source["observations"][0]["topic"], "")  # 対応関係を推測しない
        self.assertEqual(self.library.db.execute("PRAGMA user_version").fetchone()[0], 2)
        self.library.add(record("b", "2", title="ABの比較"))
        self.assertEqual(self.library.stats()["items"], 2)

    def test_future_schema_version_is_not_downgraded(self):
        self.library.db.execute("PRAGMA user_version=999")
        self.library.db.commit()
        self.library.close()
        with self.assertRaisesRegex(ValueError, "新しいデータベース"):
            Library(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)
            connection.execute("PRAGMA user_version=2")
        self.library = Library(self.path)

    def test_unlimited_search_has_no_silent_ten_thousand_cap(self):
        self.library.db.executemany(
            "INSERT INTO items(title,title_key,created_at,last_seen_at) VALUES(?,?,?,?)",
            [(f"架空目録 {index}", str(index), "2026-01-01", "2026-01-01") for index in range(10001)])
        self.library.db.commit()
        self.assertEqual(len(self.library.search(limit=None)), 10001)
        self.assertEqual(len(self.library.search(limit=2)), 2)
        with self.assertRaises(ValueError):
            self.library.search(limit=0)


class IdentifierRegressionTests(unittest.TestCase):
    def test_doi_suffix_punctuation_is_significant(self):
        self.assertEqual(normalize_doi("10.1234/ABC(DEF)"), "10.1234/abc(def)")
        self.assertNotEqual(normalize_doi("10.1234/ABC."), normalize_doi("10.1234/ABC"))
        self.assertEqual(normalize_doi("https://doi.org/10.1234/ABC%28DEF%29?x=1#fragment"),
                         "10.1234/abc(def)")

    def test_doi_only_accepts_explicit_identifier_forms(self):
        for value in ("10.1234/ABC", " DOI: 10.1234/ABC ", "https://dx.doi.org/10.1234/ABC"):
            with self.subTest(value=value):
                self.assertEqual(normalize_doi(value), "10.1234/abc")
        for value in ("https://archive.example.test/records/10.1234/collection",
                      "資料の識別子は10.1234/ABCです", "参考: 10.1234/ABC",
                      "https://doi.org.evil.test/10.1234/ABC",
                      "https://user:secret@doi.org/10.1234/ABC",
                      "10.1234/ABC\x00DEF"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_doi(value))
        self.assertEqual(normalize_doi("10.1234/SICI(1998)1:2<3::ABC>;2-X"),
                         "10.1234/sici(1998)1:2<3::abc>;2-x")

    def test_early_publication_year_is_not_dropped(self):
        self.assertEqual(find_year("1456年"), 1456)
        self.assertEqual(find_year("0263"), 263)
        self.assertEqual(record(year=1456).validated().year, 1456)
        self.assertIsNone(find_year("年不明"))

    def test_secret_query_parameters_are_not_saved_as_source_urls(self):
        self.assertEqual(safe_url("https://example.test/1?appid=secret&id=2&access_token=secret"),
                         "https://example.test/1?id=2")

    def test_provider_id_url_cannot_preserve_credentials_or_app_id(self):
        value = record(key="https://example.test/1?appid=secret&id=2").validated()
        self.assertEqual(value.provider_id, "https://example.test/1?id=2")
        with self.assertRaises(ValueError):
            record(key="https://user:secret@example.test/1").validated()


if __name__ == "__main__":
    unittest.main()
