"""史料室CLI: init / collect / sync / search / stats / export / serve。"""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote, quote_plus
import warnings
from xml.etree.ElementTree import ParseError

from .client import FetchError, get_xml
from .providers import PARSERS, request_url
from .storage import Library, utcnow

DEFAULT_DB = "data/shiryoshitsu.db"


class JapaneseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "コマンド"
        self._optionals.title = "オプション"
        if add_help:
            self.add_argument("-h", "--help", action="help", help="使い方を表示して終了")

    def format_usage(self):
        return super().format_usage().replace("usage:", "使い方:", 1)

    def format_help(self):
        return super().format_help().replace("usage:", "使い方:", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"引数エラー: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    p = JapaneseParser(prog="shiryoshitsu", description="歴史研究用の書誌・史料目録をAPIから収集して検索する")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLiteの保存先（初期値: {DEFAULT_DB}）")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="空のSQLiteデータベースを作成")
    for cmd in ("collect", "sync"):
        s = sub.add_parser(cmd, help="単発検索を収集" if cmd == "collect" else "topics.jsonに沿ってまとめて収集")
        s.add_argument("--source", default="all", help="allまたはカンマ区切り（jstage,cinii,ndl）")
        s.add_argument("--limit", type=int, default=10, help="各API・検索語あたり取得する最大件数（1〜100）")
        s.add_argument("--delay", type=float, default=2.0, help="APIへのリクエスト間隔（秒; 初期値2秒）")
        s.add_argument("--timeout", type=float, default=15, help="HTTPタイムアウト（秒）")
        s.add_argument("--log-file", default="logs/collect.jsonl", help="失敗・警告を記録するJSONL（空文字で無効）")
    c = sub.choices["collect"]
    c.add_argument("--query", required=True, help="検索語")
    c.add_argument("--topic", default="", help="収集した書誌に付ける研究テーマ")
    s = sub.choices["sync"]
    s.add_argument("--config", default="topics.json", help="検索語を定義するJSON設定")
    q = sub.add_parser("search", help="保存した書誌を日本語の部分文字列で検索")
    q.add_argument("query", nargs="?", default="", help="タイトル、著者、雑誌名、テーマなど")
    q.add_argument("--topic", default="")
    q.add_argument("--source", choices=["", *PARSERS.keys()], default="")
    q.add_argument("--from-year", type=int)
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--json", action="store_true", help="JSONとして出力")
    sub.add_parser("stats", help="件数を表示")
    e = sub.add_parser("export", help="検索結果をCSVまたはJSONへ書き出す")
    e.add_argument("--format", choices=("csv", "json"), default="csv")
    e.add_argument("--out", required=True)
    e.add_argument("--query", default="")
    e.add_argument("--topic", default="")
    v = sub.add_parser("serve", help="ローカル検索画面を表示（読み取り専用）")
    v.add_argument("--host", default="127.0.0.1", help="127.0.0.1またはlocalhostのみ許可")
    v.add_argument("--port", type=int, default=8765)
    return p


def _sources(value: str) -> list[str]:
    found = list(PARSERS) if value.strip() == "all" else [v.strip() for v in value.split(",")]
    if not found or any(name not in PARSERS for name in found):
        raise ValueError("--sourceはallまたはjstage,cinii,ndlの組合せです")
    return list(dict.fromkeys(found))


def _load_tasks(path: str) -> list[tuple[str, str]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("設定ファイルの最上位はJSONオブジェクトで指定してください")
    topics = raw.get("topics", [])
    if not isinstance(topics, list):
        raise ValueError("topicsは配列で指定してください")
    tasks: list[tuple[str, str]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("topicsの各要素はオブジェクトで指定してください")
        label = topic.get("name", "")
        queries = topic.get("queries", [])
        if not isinstance(label, str) or not label.strip() or not isinstance(queries, list):
            raise ValueError("各topicのnameとqueriesを確認してください")
        for term in queries:
            if not isinstance(term, str) or not term.strip():
                raise ValueError("queriesには空でない文字列を指定してください")
            tasks.append((label.strip(), term.strip()))
    if not tasks:
        raise ValueError("収集対象の検索語がありません")
    return list(dict.fromkeys(tasks))


def _redact(message: object, secret: str = "") -> str:
    text = str(message)
    if secret:
        for value in sorted({secret, quote(secret, safe=""), quote_plus(secret)}, key=len, reverse=True):
            text = text.replace(value, "[非表示]")
    text = re.sub(r"(?i)(appid|api_key|access_token|token)=([^&\s]+)", r"\1=[非表示]", text)
    return " ".join(text.split())[:1000]


def _diagnostic(kind: str, provider: str, topic: str, term: str, message: object,
                log_file: str, secret: str) -> None:
    event = {"time": utcnow(), "level": kind, "provider": provider,
             "topic": _redact(topic, secret), "query": _redact(term, secret),
             "message": _redact(message, secret)}
    print(f"[{kind}] {provider}: {event['message']}", file=sys.stderr)
    if log_file:
        try:
            target = Path(log_file).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[WARN] 診断ログを書き込めません。標準エラー出力のみで継続します: {_redact(exc, secret)}", file=sys.stderr)


def _collect(library: Library, tasks: list[tuple[str, str]], providers: list[str], *, limit: int, delay: float, timeout: float, log_file: str = "") -> int:
    if not 1 <= limit <= 100 or not math.isfinite(delay) or delay < 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("--limitは1〜100、--delayは有限の0以上、--timeoutは有限の正数で指定してください")
    if any(not term.strip() for _, term in tasks):
        raise ValueError("検索語は必須です")
    app_id = os.environ.get("CINII_APP_ID", "").strip()
    skipped = 0
    errors = 0
    queried = 0
    succeeded = 0
    total_seen, new_items, new_appearances = 0, 0, 0
    previous_request: float | None = None
    for topic, term in tasks:
        for provider in providers:
            if provider == "cinii" and not app_id:
                skipped += 1
                if skipped == 1:
                    _diagnostic("SKIP", provider, topic, term, "CINII_APP_IDが未設定です（ほかのAPIは実行します）", log_file, app_id)
                continue
            url = request_url(provider, term, limit, app_id)
            if previous_request is not None:
                time.sleep(max(0.0, delay - (time.monotonic() - previous_request)))
            print(f"[GET] {provider}: {_redact(term, app_id)} / {_redact(topic or '自由検索', app_id)}", flush=True)
            queried += 1
            try:
                payload = get_xml(url, timeout=timeout, minimum_delay=delay)
                with warnings.catch_warnings(record=True) as notices:
                    warnings.simplefilter("always")
                    rows = PARSERS[provider](payload)
                for notice in notices:
                    _diagnostic("WARN", provider, topic, term, notice.message, log_file, app_id)
                succeeded += 1
                for item in rows:
                    try:
                        new, new_source = library.add(item, topic=topic, query=term)
                    except ValueError as exc:
                        errors += 1
                        _diagnostic("ERROR", provider, topic, term, f"書誌の保存をスキップ: {exc}", log_file, app_id)
                        continue
                    total_seen += 1
                    new_items += int(new)
                    new_appearances += int(new_source)
                print(f"  取得 {len(rows)} 件")
                if len(rows) >= limit:
                    print("  指定件数に達しました。取得対象は先頭の候補のみです。必要に応じ検索語を絞ってください。")
            except (FetchError, ParseError, ValueError) as exc:
                errors += 1
                _diagnostic("ERROR", provider, topic, term, f"{type(exc).__name__}: {exc}", log_file, app_id)
            finally:
                # 内部再試行の終了後も間隔を設け、次のAPIへ連続送信しない。
                previous_request = time.monotonic()
    print(f"収集完了: 保存処理 {total_seen}件 / 新規書誌 {new_items}件 / 新規出典 {new_appearances}件 / 失敗 {errors}件 / スキップ {skipped}件")
    if not queried or not succeeded:
        return 1
    return 2 if errors or skipped else 0


def _export(items: list[dict], fmt: str, output: str) -> None:
    target = Path(output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        target.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        fields = ("id", "title", "authors", "year", "publication", "doi", "url", "material_type", "pages", "topics", "sources")
        with target.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for item in items:
                values = {
                    **{key: item.get(key, "") for key in fields},
                    "authors": "; ".join(item["authors"]),
                    "topics": "; ".join(item["topics"]),
                    "sources": json.dumps(item["sources"], ensure_ascii=False),
                }
                # 表計算ソフトが外部由来の値を数式として実行することを防ぐ。
                writer.writerow({key: ("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                                 for key, value in values.items()})
    print(f"書き出しました: {target} ({len(items)}件)")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream in (sys.__stdout__, sys.__stderr__) and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    try:
        if args.command in ("export", "collect", "sync"):
            output = args.out if args.command == "export" else args.log_file
            database = Path(args.db).expanduser().resolve()
            if output and Path(output).expanduser().resolve() in {database, Path(str(database) + "-wal"), Path(str(database) + "-shm")}:
                raise ValueError("出力・ログ先にデータベースとその補助ファイルは指定できません")
        with Library(args.db) as library:
            if args.command == "init":
                print(f"データベースを作成しました: {args.db}")
                return 0
            if args.command in ("collect", "sync"):
                tasks = [(args.topic, args.query)] if args.command == "collect" else _load_tasks(args.config)
                return _collect(library, tasks, _sources(args.source), limit=args.limit, delay=args.delay, timeout=args.timeout, log_file=args.log_file)
            if args.command == "search":
                result = library.search(args.query, topic=args.topic, provider=args.source, from_year=args.from_year, limit=args.limit)
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"検索結果: {len(result)}件")
                    for r in result:
                        print(f"[{r['id']}] {r['title']} ({r['year'] or '年不明'})")
                        print(f"    著者: {', '.join(r['authors']) or '未記載'} | {r['publication']}")
                        print(f"    DOI: {r['doi'] or '-'} | {r['url']}")
                        print(f"    テーマ: {', '.join(r['topics'])} | 出典: {', '.join(s['provider'] for s in r['sources'])}")
                return 0
            if args.command == "stats":
                print(json.dumps(library.stats(), ensure_ascii=False, indent=2))
                return 0
            if args.command == "export":
                _export(library.search(args.query, topic=args.topic, limit=None), args.format, args.out)
                return 0
            if args.command == "serve":
                from .webui import serve
                serve(library, host=args.host, port=args.port)
                return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"エラー: {_redact(exc, os.environ.get('CINII_APP_ID', '').strip())}", file=sys.stderr)
        return 1
    return 1
