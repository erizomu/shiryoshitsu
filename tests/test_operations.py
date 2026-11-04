import contextlib
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from shiryoshitsu import cli
from shiryoshitsu.client import FetchError
from shiryoshitsu.models import Bibliography
from shiryoshitsu.operations import (prepare_catalog, collect_catalog, backup_catalog,
                                    run_daily, run_lock, recent, status_summary, scheduled_day)
from shiryoshitsu.providers import request_url
from shiryoshitsu.storage import Library
from shiryoshitsu.webui import render
from test_providers import JSTAGE


def feed(provider, ids=(), total=None):
    total = len(ids) if total is None else total
    if provider == 'cinii':
        entries = ''.join(f'<entry><id>https://cir.nii.ac.jp/crid/{i}</id><title>史料{i}</title></entry>' for i in ids)
        return f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:os="http://a9.com/-/spec/opensearch/1.1/"><os:totalResults>{total}</os:totalResults>{entries}</feed>'.encode()
    entries = ''.join(f'<item><guid>https://ndlsearch.ndl.go.jp/books/{i}</guid><title>史料{i}</title></item>' for i in ids)
    return f'<rss><channel><totalResults>{total}</totalResults>{entries}</channel></rss>'.encode()


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'catalog.db')
        prepare_catalog(self.library)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.library.close()
        self.temp.cleanup()

    def add(self, title='史料', provider='cinii'):
        self.library._managed_write = True
        try:
            return self.library.add(Bibliography(provider=provider, provider_id=title, title=title))
        finally:
            self.library._managed_write = False

    def test_manual_writes_and_jstage_are_refused(self):
        for provider in ('jstage', 'cinii', 'ndl'):
            with self.assertRaises(ValueError):
                self.library.add(Bibliography(provider=provider, provider_id='x', title='x'))
        with self.assertRaises(ValueError):
            self.add(provider='jstage')
        self.assertEqual(self.library.stats()['items'], 0)

    def test_nonempty_legacy_db_refused_and_preserved(self):
        with Library(self.root / 'old.db') as old:
            old.add(Bibliography(provider='jstage', provider_id='old', title='既存書誌'))
            with self.assertRaises(ValueError):
                prepare_catalog(old)
            self.assertEqual(old.search()[0]['title'], '既存書誌')

    def test_incompatible_policy_refused(self):
        with self.library.db:
            self.library.db.execute("UPDATE operation_policy SET policy_json='{}'")
        with self.assertRaises(ValueError):
            prepare_catalog(self.library)

    def test_provider_parameters_and_bounds(self):
        q = parse_qs(urlsplit(request_url('ndl', '姜維', 100, start=401, ndl_provider='zassaku')).query)
        self.assertEqual(q['dpid'], ['zassaku'])
        self.assertEqual(q['idx'], ['401'])
        q = parse_qs(urlsplit(request_url('cinii', '姜維', 100, 'test', start=101, newest=True)).query)
        self.assertEqual(q['sortorder'], ['0'])
        for options in ({'start': 0}, {'start': 501}, {'ndl_provider': 'jstage'}):
            with self.assertRaises(ValueError):
                request_url('ndl', '史料', 100, **options)

    def test_pagination_continues_across_known_records_and_scopes(self):
        urls = []
        def fetch(url, **kwargs):
            urls.append(url)
            q = parse_qs(urlsplit(url).query)
            if 'cinii' in url or 'cir.nii' in url:
                start = int(q['start'][0])
                return feed('cinii', [start, start + 1] if start == 1 else [start], 3)
            return feed('ndl')
        with patch.dict(os.environ, {'CINII_APP_ID': 'test-id'}), patch('shiryoshitsu.operations.get_xml', side_effect=fetch), patch('shiryoshitsu.operations.time.sleep'):
            first, details = collect_catalog(self.library, [('三国', '姜維')], page_size=2, pages=3)
            again, _ = collect_catalog(self.library, [('三国', '姜維')], page_size=2, pages=3)
        self.assertEqual((first, again), (0, 0))
        self.assertEqual(self.library.stats()['items'], 3)
        self.assertEqual(details[0]['new_count'], 3)
        scopes = {parse_qs(urlsplit(u).query)['dpid'][0] for u in urls if 'ndlsearch' in u}
        self.assertEqual(scopes, {'zassaku', 'zassaku-online', 'iss-ndl-opac'})
        self.assertFalse(any('api.jstage' in u for u in urls))

    def test_cap_is_partial_and_visible(self):
        def fetch(url, **kwargs):
            return feed('cinii', ['one'], 10) if 'cir.nii' in url else feed('ndl')
        with patch.dict(os.environ, {'CINII_APP_ID': 'test'}), patch('shiryoshitsu.operations.get_xml', side_effect=fetch), patch('shiryoshitsu.operations.time.sleep'):
            code, details = collect_catalog(self.library, [('三国', '姜維')], pages=1, page_size=1)
        self.assertEqual(code, 2)
        self.assertIn('上限', details[0]['message'])

    def test_repeated_pages_stop_without_claiming_completion(self):
        with patch.dict(os.environ, {'CINII_APP_ID': 'test'}), patch('shiryoshitsu.operations.get_xml', side_effect=lambda u, **k: feed('cinii', ['one'], 10) if 'cir.nii' in u else feed('ndl')), patch('shiryoshitsu.operations.time.sleep'):
            code, details = collect_catalog(self.library, [('三国', '姜維')], pages=5, page_size=1)
        self.assertEqual(code, 2)
        self.assertEqual(details[0]['pages'], 2)

    def test_missing_id_does_not_skip_ndl(self):
        with patch.dict(os.environ, {'CINII_APP_ID': ''}), patch('shiryoshitsu.operations.get_xml', return_value=feed('ndl')) as fetch, patch('shiryoshitsu.operations.time.sleep'):
            code, details = collect_catalog(self.library, [('三国', '姜維')])
        self.assertEqual(code, 2)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(details[0]['status'], 'failed')

    def test_failure_details_do_not_include_secret_or_payload(self):
        with patch.dict(os.environ, {'CINII_APP_ID': 'secret/+value'}), patch('shiryoshitsu.operations.get_xml', side_effect=FetchError('appid=secret/+value private-payload')), patch('shiryoshitsu.operations.time.sleep'):
            code, details = collect_catalog(self.library, [('三国', '姜維')])
        self.assertEqual(code, 1)
        self.assertNotIn('secret', json.dumps(details))
        self.assertNotIn('private-payload', json.dumps(details))

    def test_invalid_limits_never_fetch(self):
        with patch('shiryoshitsu.operations.get_xml') as fetch:
            for options in ({'pages': 0}, {'pages': 6}, {'page_size': 101}, {'delay': 0}, {'delay': float('nan')}):
                with self.assertRaises(ValueError):
                    collect_catalog(self.library, [('三国', '姜維')], **options)
            fetch.assert_not_called()

    def test_snapshot_includes_wal_data_and_restores(self):
        self.add()
        path = backup_catalog(self.library, self.root / 'backups')
        with Library(path) as restored:
            self.assertEqual(restored.search()[0]['title'], '史料')
            self.assertEqual(restored.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_backup_retention_and_same_day_replacement(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for week in range(10):
            backup_catalog(self.library, self.root / 'backups', start + timedelta(weeks=week))
        for day in range(20):
            backup_catalog(self.library, self.root / 'backups', start + timedelta(weeks=10, days=day))
        self.assertEqual(len(list((self.root / 'backups').glob('daily-*.db'))), 14)
        self.assertEqual(len(list((self.root / 'backups').glob('weekly-*.db'))), 8)
        backup_catalog(self.library, self.root / 'backups', start + timedelta(weeks=10, days=19))
        self.assertEqual(len(list((self.root / 'backups').glob('daily-*.db'))), 14)

    def test_lock_exclusion_and_release_after_exception(self):
        with self.assertRaises(RuntimeError):
            with run_lock(self.root):
                with self.assertRaises(ValueError):
                    with run_lock(self.root):
                        pass
                raise RuntimeError()
        with run_lock(self.root):
            pass

    def test_daily_once_per_day_force_and_backup_failure(self):
        config = self.root / 'topics.json'
        config.write_text(json.dumps({'topics': [{'name': '三国', 'queries': ['姜維']}]}), encoding='utf-8')
        home = self.root / 'data-home'
        with patch('shiryoshitsu.operations.collect_catalog', return_value=(0, [])) as collect:
            self.assertEqual(run_daily(str(config), home), 0)
            self.assertEqual(run_daily(str(config), home), 0)
            self.assertEqual(collect.call_count, 1)
            with patch('shiryoshitsu.operations.backup_catalog', side_effect=OSError('disk')):
                self.assertEqual(run_daily(str(config), home, force=True), 1)
        with Library(home / 'data/catalog.db') as lib:
            summary = status_summary(lib)
            self.assertEqual(summary['status'], 1)
            self.assertEqual(summary['consecutive_issues'], 1)
            self.assertTrue(summary['last_backup'])
            self.assertIn('バックアップ', render(lib))

    def test_external_snapshot_and_invalid_nested_destination(self):
        config = self.root / 'topics.json'
        config.write_text(json.dumps({'topics': [{'name': '三国', 'queries': ['姜維']}]}), encoding='utf-8')
        home = self.root / 'home'
        with patch('shiryoshitsu.operations.collect_catalog', return_value=(2, [])):
            self.assertEqual(run_daily(str(config), home, backup_dir=self.root / 'external'), 2)
        self.assertTrue(list((self.root / 'external').glob('daily-*.db')))
        with self.assertRaises(ValueError):
            run_daily(str(config), home, backup_dir=home / 'data')

    def test_failed_collection_does_not_replace_backup(self):
        config = self.root / 'topics.json'
        config.write_text(json.dumps({'topics': [{'name': '三国', 'queries': ['姜維']}]}), encoding='utf-8')
        with patch('shiryoshitsu.operations.collect_catalog', return_value=(1, [])), patch('shiryoshitsu.operations.backup_catalog') as backup:
            self.assertEqual(run_daily(str(config), self.root / 'home'), 1)
            backup.assert_not_called()

    def test_newly_collected_old_publication_is_recent(self):
        self.add('古い刊行年の新着')
        with self.library.db:
            self.library.db.execute('UPDATE items SET year=1900')
        self.assertEqual(len(recent(self.library)), 1)
        with self.library.db:
            self.library.db.execute("UPDATE items SET created_at='2020-01-01T00:00:00+00:00'")
        self.assertEqual(recent(self.library), [])
        self.assertIn('表示 0件', render(self.library, new_only=True))

    def test_preview_does_not_create_selected_db_or_log(self):
        db = self.root / 'must-not-exist.db'
        with patch('shiryoshitsu.cli.get_xml', return_value=JSTAGE):
            self.assertEqual(cli.main(['--db', str(db), 'preview', '--query', '史料']), 0)
        self.assertFalse(db.exists())

    def test_managed_db_manual_collect_refused_before_network(self):
        with patch('shiryoshitsu.cli.get_xml') as fetch, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['--db', str(self.root / 'catalog.db'), 'collect', '--query', '史料']), 1)
            fetch.assert_not_called()

    def test_status_escapes_external_values(self):
        with self.library.db:
            self.library.db.execute("INSERT INTO collection_runs(started_at,local_day,status,details) VALUES(?,?,?,?)",
                                    ('<script>x</script>', '2026-11-05', 2,
                                     json.dumps([{'status': 'partial', 'message': '<script>bad</script>'}])))
        html = render(self.library, new_only=True)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('name="new" value="1"', html)

    def test_logon_catchup_does_not_consume_tonights_run(self):
        # UTCから日本時間へ変換。朝のログオンは前夜分、19:17から当日分。
        self.assertEqual(scheduled_day(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)), '2026-11-04')
        self.assertEqual(scheduled_day(datetime(2026, 11, 5, 10, 16, tzinfo=timezone.utc)), '2026-11-04')
        self.assertEqual(scheduled_day(datetime(2026, 11, 5, 10, 17, tzinfo=timezone.utc)), '2026-11-05')
