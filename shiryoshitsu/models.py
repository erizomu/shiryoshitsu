"""収集する書誌レコードの共通形式と、保守的な照合キー。"""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
_SECRET_PARAMS = frozenset({"appid", "app_id", "api_key", "apikey", "access_token", "token", "password"})


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def normalize(value: str | None) -> str:
    return unicodedata.normalize("NFKC", clean_text(value)).casefold()


def identity_text(value: str | None) -> str:
    """照合用。全半角・大文字小文字・空白のみ正規化し、句読点を残す。"""
    return "".join(ch for ch in normalize(value) if not ch.isspace())


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith(("https://", "http://")):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        if parts.netloc.casefold() not in ("doi.org", "dx.doi.org"):
            return None
        value = unquote(parts.path.removeprefix("/"))
    elif value.lower().startswith("doi:"):
        value = value[4:].strip()
    # 一般の識別子URLや説明文の一部をDOIと誤認すると、別書誌を統合してしまう。
    if not _DOI_RE.fullmatch(value) or any(unicodedata.category(char).startswith("C") for char in value):
        return None
    # DOI末尾の括弧・句読点も識別子の一部になり得る。推測で削除しない。
    return value.lower()


def find_year(value: str | None) -> int | None:
    match = _YEAR_RE.search(value or "")
    year = int(match.group(1)) if match else 0
    return year or None


def safe_url(value: str | None) -> str:
    """未検証の書誌提供元データを、Web画面のリンクとして安全に扱う。"""
    if not value:
        return ""
    v = value.strip()
    try:
        parts = urlsplit(v)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc or parts.username or parts.password:
            return ""
        # APIの検索用アクセストークン等がURLに入る場合の混入を防ぐ。
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = urlencode([(key, val) for key, val in pairs if key.casefold() not in _SECRET_PARAMS])
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))
    except ValueError:
        return ""


@dataclass(frozen=True)
class Bibliography:
    provider: str
    provider_id: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    publication: str = ""
    doi: str | None = None
    url: str = ""
    material_type: str = ""
    pages: str = ""
    source_institution: str = ""

    def validated(self) -> "Bibliography":
        """不完全な行はデータベースへ入れない。"""
        from dataclasses import replace
        title = clean_text(self.title)
        provider_id = clean_text(self.provider_id)
        if provider_id.lower().startswith(("http://", "https://")):
            provider_id = safe_url(provider_id)
        if not title or not provider_id or not clean_text(self.provider):
            raise ValueError("provider、provider_id、title は必須です")
        if self.year is not None and (isinstance(self.year, bool) or not isinstance(self.year, int) or not (1 <= self.year <= 9999)):
            raise ValueError("出版年が不正です")
        return replace(
            self, title=title, provider_id=provider_id,
            authors=tuple(filter(None, (clean_text(a) for a in self.authors))),
            publication=clean_text(self.publication), doi=normalize_doi(self.doi),
            url=safe_url(self.url), material_type=clean_text(self.material_type),
            pages=clean_text(self.pages), source_institution=clean_text(self.source_institution),
        )

    @property
    def metadata_key(self) -> str | None:
        # 年・第一著者・タイトルが全て揃う場合のみDOI無しの横断照合を許可。
        if self.year and self.authors and identity_text(self.title) and identity_text(self.authors[0]):
            return f"{identity_text(self.title)}|{self.year}|{identity_text(self.authors[0])}"
        return None
