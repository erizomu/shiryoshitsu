"""外部JS・CSS・認証なしの、ローカル用読み取り専用検索画面。"""
from __future__ import annotations
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from .models import safe_url
from .storage import Library

CSS = """
:root{font-family:-apple-system,BlinkMacSystemFont,'Noto Sans JP',sans-serif;color:#22313d;background:#f5f7fa}
*{box-sizing:border-box}body{margin:0 auto;max-width:970px;padding:18px}
header{padding:20px 0;border-bottom:1px solid #dce3eb}h1{margin:0 0 8px;font-size:1.7rem}
small,.meta{color:#4c6070}form{display:flex;gap:8px;flex-wrap:wrap;margin:22px 0}
input{padding:13px;border:1px solid #bbc6cf;border-radius:8px;min-width:210px;flex:1;font-size:1rem}
button{padding:12px 20px;border:0;border-radius:8px;background:#174f77;color:white;font-weight:600;cursor:pointer}
.card{background:white;margin:14px 0;padding:18px 20px;border:1px solid #e0e5eb;border-radius:12px;overflow-wrap:anywhere}
a{color:#14588a}.title{font-size:1.1rem;font-weight:700}.meta{margin:9px 0 4px;line-height:1.7}
.tags{font-size:.85rem;color:#596b79}footer{margin:36px 0 20px;color:#67727d;font-size:.85rem}
@media(max-width:480px){body{padding:12px}form{display:block}input,button{width:100%;margin-bottom:8px}h1{font-size:1.4rem}}
"""


def _result_card(item: dict) -> str:
    title = escape(item["title"])
    # 古いDBや手動で編集されたDBのURLも、表示の直前に検査する。
    url = escape(safe_url(item["url"]), quote=True)
    link = (f'<a href="{url}" rel="noopener noreferrer" target="_blank">{title}</a>' if url else title)
    author = escape("、".join(item["authors"]) or "著者不詳")
    year = escape(str(item["year"] or "出版年不詳"))
    pub = escape(item["publication"])
    doi = escape(item["doi"] or "")
    topics = escape(" / ".join(item["topics"]))
    names = {"jstage": "J-STAGE（JST）", "cinii": "CiNii Research", "ndl": "NDLサーチ"}
    sources = escape("、".join(names.get(s["provider"], s["provider"]) +
                              (" / " + s["source_institution"] if s.get("source_institution") else "")
                              for s in item["sources"]))
    return (f'<article class="card"><div class="title">{link}</div>'
            f'<div class="meta">{author} | {year} | {pub}</div>'
            f'<div class="tags">{topics}　出典: {sources}</div>'
            f'<div class="meta">{("DOI: " + doi) if doi else ""}</div></article>')


def render(library: Library, query: str = "", *, new_only: bool = False) -> str:
    from .operations import recent, status_summary
    items = recent(library, 7, query)[:200] if new_only else library.search(query, limit=200)
    stats = library.stats()
    status = status_summary(library)
    panel = ""
    if status:
        labels = {None: "実行中／前回中断", 0: "正常", 1: "失敗", 2: "一部失敗・取得上限あり"}
        issues = [d for d in status["details"] if d.get("status") != "ok"]
        issue_text = " / ".join(dict.fromkeys(f"{d.get('provider', '')} {d.get('scope', '')}: {d.get('message', '')}" for d in issues))
        panel = (f'<section class="card">前回収集: {escape(status["started_at"])}（UTC） / '
                 f'{labels.get(status["status"], "不明")} / 新規書誌 {status["new_count"]}件<br>'
                 f'最終バックアップ: {escape(status["last_backup"])}（UTC）<br>{escape(issue_text)}</section>')
    body = "\n".join(_result_card(item) for item in items) if items else '<p>一致する書誌はありません。</p>'
    new_filter = '<input type="hidden" name="new" value="1">' if new_only else ''
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>史料室 | 書誌検索</title>
<style>{CSS}</style></head><body><header><h1>史料室 — 文献・史料目録</h1>
<small>{stats['items']}件の書誌・{stats['sources']}件の出典。本文やPDFを保存するシステムではありません。</small></header>
<main>{panel}<p><a href="/?new=1">最近7日で初めて取得した書誌</a> / <a href="/">全書誌</a></p><form action="/" method="get">{new_filter}<input name="q" type="search" value="{escape(query, quote=True)}" placeholder="姜維、松下之綱、山科言継…" aria-label="書誌を検索">
<button type="submit">検索</button></form><p>表示 {len(items)}件（最大200件）</p>{body}</main>
<footer><a href="https://www.jstage.jst.go.jp/">Powered by J-STAGE</a>（JST提供）。
<a href="https://www.jstage.jst.go.jp/static/pages/WebAPI/-char/ja">J-STAGE Web API利用規約</a>に従って利用してください。
NDLサーチAPIを使用。各書誌の詳細・利用条件はリンク先の提供機関で確認してください。</footer></body></html>"""


def make_handler(library: Library) -> type[BaseHTTPRequestHandler]:
    """ソケット接続なしでも検証できる、閲覧専用のHTTPハンドラ。"""
    class Handler(BaseHTTPRequestHandler):
        server_version = "Shiryoshitsu"
        sys_version = ""

        def _respond(self, status: int, data: bytes, *, html: bool = False) -> None:
            self.send_response(status)
            content_type = "text/html" if html else "text/plain"
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            if status == 405:
                self.send_header("Allow", "GET")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            self._respond(status, (message + "\n").encode("utf-8"))

        def _local_host(self) -> bool:
            # ループバックへのDNS再束縛による、外部サイトからのDB参照を防ぐ。
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                return False
            port = self.server.server_port
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                allowed.update(("127.0.0.1", "localhost"))
            return hosts[0].lower() in allowed

        def do_GET(self) -> None:
            if not self._local_host():
                self._error(403, "この画面はlocalhostまたは127.0.0.1から利用してください。")
                return
            try:
                parsed = urlsplit(self.path)
            except ValueError:
                self._error(400, "リクエストのURLが不正です。")
                return
            if parsed.scheme or parsed.netloc:
                self._error(400, "ローカル画面のパスを指定してください。")
                return
            if parsed.path not in ("/", "/index.html"):
                self._error(404, "ページが見つかりません。")
                return
            try:
                params = parse_qs(parsed.query, max_num_fields=20)
                query = params.get("q", [""])[0][:200]
            except ValueError:
                self._error(400, "検索条件が多すぎます。")
                return
            data = render(library, query, new_only=params.get("new") == ["1"]).encode("utf-8")
            self._respond(200, data, html=True)

        def _reject_method(self) -> None:
            self._error(405, "この画面は閲覧専用です。GETを使用してください。")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject_method

        def log_message(self, _format: str, *_args) -> None:
            # 検索語を含むURLを、HTTPアクセスログへ残さない。
            pass

    return Handler


def serve(library: Library, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host.lower() not in ("127.0.0.1", "localhost"):
        raise ValueError("Web画面はローカル専用です。--hostは127.0.0.1またはlocalhostを指定してください")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("--portは1〜65535を指定してください")
    # localhostの名前解決設定に依存せず、必ずIPv4ループバックへbindする。
    with HTTPServer(("127.0.0.1", port), make_handler(library)) as server:
        print(f"ローカル検索: http://127.0.0.1:{port}/ (Ctrl+Cで停止)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
