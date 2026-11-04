"""PC用の承認後運用。登録・自動有効化は行わない。API応答の生データは保存しない。"""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import warnings
from xml.etree.ElementTree import ParseError

from .client import FetchError, get_xml
from .providers import request_url, PARSERS, _parse_xml
from .storage import Library, utcnow

POLICY = {
    "version": 1,
    "checked": "2026-11-05",
    "cinii": {"scope": "本人の研究用メタデータ。再配布許可を意味しない",
              "terms": "https://support.nii.ac.jp/ja/cinii/terms"},
    "ndl": {"providers": ["zassaku", "zassaku-online", "iss-ndl-opac"],
            "license": "CC BY（提供元一覧・注記に従う）",
            "credit": "国立国会図書館サーチAPI／国立国会図書館作成書誌",
            "terms": "https://ndlsearch.ndl.go.jp/help/api/provider"},
    "jstage": "永続保存対象外。previewで一時閲覧のみ",
}


def data_home() -> Path:
    if os.name == "nt":
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "Shiryoshitsu"


def scheduled_day(now: datetime | None = None) -> str:
    """直近に到来した日本時間19:17の予定日。朝のログオンで今夜分を消費しない。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=9)))
    day = now.date()
    if (now.hour, now.minute) < (19, 17):
        day -= timedelta(days=1)
    return day.isoformat()


@contextmanager
def run_lock(root: Path):
    """OSのプロセスロック。異常終了時はOSが解放し、ロックファイルは削除しない。"""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "collection.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("収集処理が既に動いています") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def prepare_catalog(library: Library) -> None:
    exists = library.db.execute("SELECT 1 FROM sqlite_master WHERE name='operation_policy'").fetchone()
    if not exists and library.stats()["items"]:
        raise ValueError("既存の手動収集DBは自動運用へ転用しません。空の専用保存先を指定してください")
    with library.db:
        library.db.execute("CREATE TABLE IF NOT EXISTS operation_policy (id INTEGER PRIMARY KEY CHECK(id=1), policy_json TEXT NOT NULL)")
        library.db.execute("INSERT OR IGNORE INTO operation_policy VALUES(1,?)", (json.dumps(POLICY, ensure_ascii=False),))
        saved = json.loads(library.db.execute("SELECT policy_json FROM operation_policy WHERE id=1").fetchone()[0])
        if saved != POLICY:
            raise ValueError("保存条件の版が違います。移行手順を確認してください")
        library.db.execute("""CREATE TABLE IF NOT EXISTS collection_runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            local_day TEXT NOT NULL, status INTEGER, new_count INTEGER NOT NULL DEFAULT 0,
            details TEXT NOT NULL DEFAULT '[]', backup_path TEXT NOT NULL DEFAULT '')""")


def _page_size(payload: bytes, provider: str) -> tuple[int, int | None]:
    root = _parse_xml(payload)
    container = root if provider == "cinii" else root.find("channel")
    tag = "{http://www.w3.org/2005/Atom}entry" if provider == "cinii" else "item"
    if container is None:
        raise ValueError("結果一覧がありません")
    totals = [x.text for x in container if x.tag.split("}")[-1] == "totalResults"]
    total = int(totals[0]) if totals and (totals[0] or "").isdigit() else None
    return len(container.findall(tag)), total


def collect_catalog(library: Library, tasks: list[tuple[str, str]], *, page_size: int = 100,
                    pages: int = 5, delay: float = 2, timeout: float = 15) -> tuple[int, list[dict]]:
    """全検索を毎回重ねて走査。公開年で除外しないので古い論文の新規登録も対象。"""
    if (type(page_size) is not int or not 1 <= page_size <= 100 or
            type(pages) is not int or not 1 <= pages <= 5 or
            not math.isfinite(delay) or delay < 2 or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("件数1〜100、ページ1〜5、間隔2秒以上、正のタイムアウトを指定してください")
    if not tasks or any(not t.strip() for _, t in tasks):
        raise ValueError("検索語がありません")
    app_id = os.environ.get("CINII_APP_ID", "").strip()
    details = []
    last_request = None
    any_success = False
    from .cli import _redact
    scopes = [("cinii", ""), *(("ndl", p) for p in POLICY["ndl"]["providers"])]
    for topic, term in tasks:
        for provider, scope in scopes:
            entry = {"provider": provider, "scope": scope, "query": term, "topic": topic,
                     "status": "ok", "pages": 0, "saved": 0, "new_count": 0}
            details.append(entry)
            if provider == "cinii" and not app_id:
                entry.update(status="failed", message="CINII_APP_IDが未設定です")
                continue
            seen = set()
            for page in range(pages):
                if last_request is not None:
                    time.sleep(max(0, delay - (time.monotonic() - last_request)))
                try:
                    url = request_url(provider, term, page_size, app_id,
                                      start=page * page_size + 1, ndl_provider=scope, newest=True)
                    payload = get_xml(url, timeout=timeout, minimum_delay=delay)
                    with warnings.catch_warnings(record=True) as notices:
                        warnings.simplefilter("always")
                        records = PARSERS[provider](payload)
                    raw_count, total = _page_size(payload, provider)
                    entry["pages"] += 1
                    any_success = True
                    if notices:
                        entry.update(status="partial", message="不完全な書誌を除外しました")
                    ids = {r.provider_id for r in records}
                    if raw_count and ids and ids <= seen:
                        entry.update(status="partial", message="ページが繰り返されたため中止しました")
                        break
                    seen.update(ids)
                    library._managed_write = True
                    try:
                        for record in records:
                            try:
                                added, _ = library.add(record, topic=topic, query=term)
                                entry["saved"] += 1
                                entry["new_count"] += int(added)
                            except ValueError:
                                entry.update(status="partial", message="同定の衝突により書誌を除外しました")
                    finally:
                        library._managed_write = False
                    if total is not None and page * page_size + raw_count >= total:
                        break
                    if raw_count < page_size:
                        break
                    if page == pages - 1:
                        entry.update(status="partial", message="取得上限に達しました。全件取得ではありません")
                except (FetchError, ParseError, ValueError) as exc:
                    # API応答やURLを永続ログへ転記しない。秘密値・提供情報の混入を防ぐ。
                    entry.update(status="failed", message=f"{type(exc).__name__}: 取得に失敗しました")
                    break
                finally:
                    last_request = time.monotonic()
    # 検索語が秘密値と偶然一致しても記録へ出さない。
    details = json.loads(_redact_json(details, app_id, _redact))
    return (1 if not any_success else 2 if any(d["status"] != "ok" for d in details) else 0), details


def _redact_json(details, secret, redact):
    return json.dumps([{k: redact(v, secret) if isinstance(v, str) else v for k, v in d.items()}
                       for d in details], ensure_ascii=False)


def backup_catalog(library: Library, directory: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"daily-{now:%Y-%m-%d}.db"
    temp = directory / f"snapshot-{os.getpid()}.tmp"
    try:
        with closing(sqlite3.connect(temp)) as destination:
            library.db.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("バックアップの整合性検査に失敗しました")
        temp.replace(target)
        week = now.isocalendar()
        weekly = directory / f"weekly-{week.year}-{week.week:02}.db"
        # 同一週の最後の正常スナップショットを保持する。
        import shutil
        shutil.copyfile(target, temp)
        temp.replace(weekly)
        for prefix, keep in (("daily", 14), ("weekly", 8)):
            for old in sorted(directory.glob(f"{prefix}-*.db"), reverse=True)[keep:]:
                old.unlink()
        return target
    finally:
        temp.unlink(missing_ok=True)


def run_daily(config: str, root: Path, *, force: bool = False, page_size: int = 100,
              pages: int = 5, backup_dir: Path | None = None) -> int:
    from .cli import _load_tasks
    # 不正設定をDBの作成前に拒否する。
    tasks = _load_tasks(config)
    root = root.expanduser().resolve()
    if backup_dir is not None:
        backup_dir = backup_dir.expanduser().resolve()
        if backup_dir == root or root in backup_dir.parents or backup_dir in root.parents:
            raise ValueError("外部バックアップはデータ保存先と別のフォルダを指定してください")
    with run_lock(root), Library(root / "data" / "catalog.db") as library:
        prepare_catalog(library)
        today = scheduled_day()
        if not force and library.db.execute(
                "SELECT 1 FROM collection_runs WHERE local_day=? AND finished_at IS NOT NULL", (today,)).fetchone():
            print("直近の予定分は実行済みです。再実行する場合は--forceを指定してください")
            return 0
        with library.db:
            run_id = library.db.execute("INSERT INTO collection_runs(started_at,local_day) VALUES(?,?)",
                                        (utcnow(), today)).lastrowid
        status, details = collect_catalog(library, tasks, page_size=page_size, pages=pages)
        new_count = sum(d.get("new_count", 0) for d in details)
        with library.db:
            library.db.execute("UPDATE collection_runs SET finished_at=?,status=?,new_count=?,details=? WHERE id=?",
                               (utcnow(), status, new_count, json.dumps(details, ensure_ascii=False), run_id))
        try:
            # 部分成功も保全。全API失敗時は正常バックアップの世代を置き換えない。
            if status != 1:
                snapshot = backup_catalog(library, root / "backups")
                if backup_dir is not None:
                    backup_catalog(library, backup_dir)
                with library.db:
                    library.db.execute("UPDATE collection_runs SET backup_path=? WHERE id=?", (str(snapshot), run_id))
        except (OSError, sqlite3.Error, ValueError):
            status = 1
            details.append({"status": "failed", "provider": "backup", "message": "バックアップに失敗しました"})
            with library.db:
                library.db.execute("UPDATE collection_runs SET status=?,details=? WHERE id=?",
                                   (status, json.dumps(details, ensure_ascii=False), run_id))
        print(json.dumps({"status": status, "new_count": new_count, "checks": details}, ensure_ascii=False, indent=2))
        return status


def status_summary(library: Library) -> dict | None:
    if not library.db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_runs'").fetchone():
        return None
    row = library.db.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    result = dict(row)
    result["details"] = json.loads(result["details"])
    # バックアップ自体に含まれる記録も表示できるよう、ファイル存在に依存しない。
    previous = library.db.execute("SELECT finished_at,backup_path FROM collection_runs WHERE backup_path!='' ORDER BY id DESC LIMIT 1").fetchone()
    result["last_backup"] = previous["finished_at"] if previous else "未記録"
    result["consecutive_issues"] = 0
    for run in library.db.execute("SELECT status FROM collection_runs ORDER BY id DESC LIMIT 30"):
        if run["status"] not in (1, 2):
            break
        result["consecutive_issues"] += 1
    return result


def recent(library: Library, days: int = 7, query: str = "") -> list[dict]:
    if not 1 <= days <= 3650:
        raise ValueError("日数は1〜3650です")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = library.search(query, limit=None)
    return sorted((r for r in rows if r["created_at"] >= since), key=lambda r: (r["created_at"], r["id"]), reverse=True)
