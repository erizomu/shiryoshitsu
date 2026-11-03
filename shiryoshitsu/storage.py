"""SQLiteの書誌、出典、検索テーマを分離して保管する。"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from .models import Bibliography, identity_text, normalize


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Library:
    def __init__(self, path: str | Path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.init()
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Library":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def init(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise ValueError("この版より新しいデータベースです。対応する史料室を使用してください")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          title_key TEXT NOT NULL,
          first_author_key TEXT NOT NULL DEFAULT '',
          authors_json TEXT NOT NULL DEFAULT '[]',
          year INTEGER,
          publication TEXT NOT NULL DEFAULT '',
          doi TEXT,
          url TEXT NOT NULL DEFAULT '',
          material_type TEXT NOT NULL DEFAULT '',
          pages TEXT NOT NULL DEFAULT '',
          search_blob TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS unique_doi ON items(doi) WHERE doi IS NOT NULL;
        CREATE INDEX IF NOT EXISTS metadata_match ON items(title_key, year, first_author_key);
        CREATE TABLE IF NOT EXISTS appearances (
          id INTEGER PRIMARY KEY,
          item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
          provider TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          source_url TEXT NOT NULL DEFAULT '',
          source_institution TEXT NOT NULL DEFAULT '',
          search_query TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          UNIQUE(provider, provider_id)
        );
        CREATE INDEX IF NOT EXISTS appearances_item ON appearances(item_id);
        CREATE TABLE IF NOT EXISTS topics (
          item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
          topic TEXT NOT NULL,
          PRIMARY KEY(item_id, topic)
        );
        """)
        with self.db:
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(appearances)")}
            if "source_institution" not in columns:
                self.db.execute("ALTER TABLE appearances ADD COLUMN source_institution TEXT NOT NULL DEFAULT ''")
            self.db.execute("""
              CREATE TABLE IF NOT EXISTS observations (
                appearance_id INTEGER NOT NULL REFERENCES appearances(id) ON DELETE CASCADE,
                search_query TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_institution TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(appearance_id, search_query, topic, source_url, source_institution)
              )
            """)
            if version < 2:
                # v1から復元できるのは直近の検索語・URLのみ。テーマとの対応は推測しない。
                self.db.execute("""
                  INSERT OR IGNORE INTO observations
                    (appearance_id,search_query,topic,source_url,source_institution,first_seen_at,last_seen_at)
                  SELECT id,search_query,'',source_url,source_institution,first_seen_at,last_seen_at FROM appearances
                """)
                for row in self.db.execute("SELECT id,title,authors_json FROM items").fetchall():
                    authors = json.loads(row["authors_json"])
                    self.db.execute("UPDATE items SET title_key=?,first_author_key=? WHERE id=?",
                                    (identity_text(row["title"]), identity_text(authors[0]) if authors else "", row["id"]))
                self.db.execute("PRAGMA user_version=2")

    def _find_item(self, record: Bibliography) -> int | None:
        # DOIが最も強い同定根拠。書誌一致では候補が一意な場合だけ結合する。
        if record.doi:
            row = self.db.execute("SELECT id FROM items WHERE doi=?", (record.doi,)).fetchone()
            if row:
                return row["id"]
        if record.metadata_key:
            rows = self.db.execute("""
              SELECT id,doi FROM items WHERE title_key=? AND year=? AND first_author_key=?
            """, (identity_text(record.title), record.year,
                  identity_text(record.authors[0]))).fetchall()
            if len(rows) == 1 and (not record.doi or not rows[0]["doi"]):
                return rows[0]["id"]
        return None

    def _merge(self, keep: int, discard: int) -> None:
        """同じDOIで後から出典同士が結びついた場合に限り既存行を統合する。"""
        if keep == discard:
            return
        old = self.db.execute("SELECT * FROM items WHERE id=?", (discard,)).fetchone()
        new = self.db.execute("SELECT * FROM items WHERE id=?", (keep,)).fetchone()
        if not old or not new or (old["doi"] and new["doi"] and old["doi"] != new["doi"]):
            raise ValueError("同定の衝突。異なるDOIのデータは統合しません")
        self.db.execute("UPDATE appearances SET item_id=? WHERE item_id=?", (keep, discard))
        self.db.execute("INSERT OR IGNORE INTO topics(item_id,topic) SELECT ?,topic FROM topics WHERE item_id=?", (keep, discard))
        self.db.execute("DELETE FROM items WHERE id=?", (discard,))
        updates = {}
        for field in ("doi", "publication", "url", "material_type", "pages", "year"):
            if not new[field] and old[field]:
                updates[field] = old[field]
        if new["authors_json"] == "[]" and old["authors_json"] != "[]":
            updates["authors_json"] = old["authors_json"]
            updates["first_author_key"] = old["first_author_key"]
        if updates:
            sql = ", ".join(f"{key}=?" for key in updates)
            self.db.execute(f"UPDATE items SET {sql} WHERE id=?", (*updates.values(), keep))
        self._reindex(keep)

    def _reindex(self, item_id: int) -> None:
        row = self.db.execute("SELECT title, authors_json, publication, doi FROM items WHERE id=?", (item_id,)).fetchone()
        authors = json.loads(row["authors_json"])
        tags = [x["topic"] for x in self.db.execute("SELECT topic FROM topics WHERE item_id=?", (item_id,))]
        blob = normalize(" ".join([row["title"], row["publication"], row["doi"] or "", *authors, *tags]))
        self.db.execute("UPDATE items SET search_blob=? WHERE id=?", (blob, item_id))

    def add(self, incoming: Bibliography, *, topic: str = "", query: str = "") -> tuple[bool, bool]:
        """戻り値は(書誌新規追加, 出典新規追加)。反復実行しても増え続けない。"""
        record = incoming.validated()
        now = utcnow()
        with self.db:
            source = self.db.execute(
                "SELECT a.id,a.item_id,i.doi FROM appearances a JOIN items i ON i.id=a.item_id WHERE a.provider=? AND a.provider_id=?",
                (record.provider, record.provider_id)).fetchone()
            from_source = source["item_id"] if source else None
            if source and source["doi"] and record.doi and source["doi"] != record.doi:
                raise ValueError("同じ出典IDに異なるDOIがあります。既存書誌を変更せず、この行を除外します")
            found = self._find_item(record)
            if from_source is not None:
                item_id = from_source
                # 書誌情報の訂正だけでは既知の出典を別書誌へ移動しない。
                if record.doi and found is not None and found != from_source:
                    matching = self.db.execute("SELECT doi FROM items WHERE id=?", (found,)).fetchone()
                    if matching["doi"] == record.doi:
                        self._merge(found, from_source)
                        item_id = found
            else:
                item_id = found
            added = item_id is None
            if added:
                cursor = self.db.execute("""
                  INSERT INTO items(title,title_key,first_author_key,authors_json,year,
                    publication,doi,url,material_type,pages,created_at,last_seen_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (record.title, identity_text(record.title),
                      identity_text(record.authors[0]) if record.authors else "",
                      json.dumps(record.authors, ensure_ascii=False), record.year,
                      record.publication, record.doi, record.url, record.material_type,
                      record.pages, now, now))
                item_id = cursor.lastrowid
            else:
                old = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                updates: dict[str, object] = {"last_seen_at": now}
                for field in ("year", "publication", "doi", "url", "material_type", "pages"):
                    if not old[field] and getattr(record, field):
                        updates[field] = getattr(record, field)
                if old["authors_json"] == "[]" and record.authors:
                    updates["authors_json"] = json.dumps(record.authors, ensure_ascii=False)
                    updates["first_author_key"] = identity_text(record.authors[0])
                sql = ", ".join(f"{key}=?" for key in updates)
                self.db.execute(f"UPDATE items SET {sql} WHERE id=?", (*updates.values(), item_id))
            source_added = from_source is None
            self.db.execute("""
              INSERT INTO appearances(item_id,provider,provider_id,source_url,source_institution,search_query,first_seen_at,last_seen_at)
              VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(provider,provider_id) DO UPDATE SET
                item_id=excluded.item_id,
                source_url=CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE appearances.source_url END,
                source_institution=CASE WHEN excluded.source_institution != '' THEN excluded.source_institution ELSE appearances.source_institution END,
                search_query=excluded.search_query,
                last_seen_at=excluded.last_seen_at
            """, (item_id, record.provider, record.provider_id, record.url, record.source_institution,
                  query, now, now))
            appearance_id = self.db.execute("SELECT id FROM appearances WHERE provider=? AND provider_id=?",
                                            (record.provider, record.provider_id)).fetchone()[0]
            self.db.execute("""
              INSERT INTO observations(appearance_id,search_query,topic,source_url,source_institution,first_seen_at,last_seen_at)
              VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(appearance_id,search_query,topic,source_url,source_institution)
              DO UPDATE SET last_seen_at=excluded.last_seen_at
            """, (appearance_id, query, topic, record.url, record.source_institution, now, now))
            if topic:
                self.db.execute("INSERT OR IGNORE INTO topics(item_id,topic) VALUES (?,?)", (item_id, topic))
            self._reindex(item_id)
        return added, source_added

    def search(self, query: str = "", *, limit: int | None = 100, topic: str = "", provider: str = "", from_year: int | None = None) -> list[dict]:
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("表示件数は正の整数で指定してください")
        where, args = [], []
        if query.strip():
            # SQL LIKEの予約文字は検索文字として扱う。
            term = normalize(query.strip()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("i.search_blob LIKE ? ESCAPE '\\'")
            args.append("%" + term + "%")
        if topic:
            where.append("EXISTS (SELECT 1 FROM topics t WHERE t.item_id=i.id AND t.topic=?)")
            args.append(topic)
        if provider:
            where.append("EXISTS (SELECT 1 FROM appearances a WHERE a.item_id=i.id AND a.provider=?)")
            args.append(provider)
        if from_year:
            where.append("i.year >= ?")
            args.append(from_year)
        sql = "SELECT i.* FROM items i" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY i.year DESC, i.id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        return [self._as_dict(r) for r in rows]

    def _as_dict(self, row: sqlite3.Row) -> dict:
        result = dict(row)
        result["authors"] = json.loads(result.pop("authors_json"))
        for hidden in ("title_key", "first_author_key", "search_blob"):
            result.pop(hidden)
        result["topics"] = [x["topic"] for x in self.db.execute(
            "SELECT topic FROM topics WHERE item_id=? ORDER BY topic", (row["id"],))]
        result["sources"] = []
        for source in self.db.execute(
                "SELECT id,provider,provider_id,source_url,source_institution,search_query,first_seen_at,last_seen_at FROM appearances WHERE item_id=? ORDER BY provider,provider_id", (row["id"],)):
            source_dict = dict(source)
            appearance_id = source_dict.pop("id")
            history = [dict(x) for x in self.db.execute("""
              SELECT search_query,topic,source_url,source_institution,first_seen_at,last_seen_at
              FROM observations WHERE appearance_id=? ORDER BY search_query,topic,source_url,source_institution
            """, (appearance_id,))]
            source_dict["queries"] = sorted({observation["search_query"] for observation in history if observation["search_query"]})
            source_dict["observations"] = history
            result["sources"].append(source_dict)
        return result

    def stats(self) -> dict:
        return {
            "items": self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "sources": self.db.execute("SELECT COUNT(*) FROM appearances").fetchone()[0],
            "topics": self.db.execute("SELECT COUNT(DISTINCT topic) FROM topics").fetchone()[0],
            "by_provider": {r["provider"]: r["count"] for r in self.db.execute(
                "SELECT provider, COUNT(*) AS count FROM appearances GROUP BY provider ORDER BY provider")},
        }
