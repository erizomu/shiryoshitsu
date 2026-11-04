"""2026年11月時点の公式仕様に基づくXML/Atom/RSS書誌アダプタ。"""
from __future__ import annotations
import warnings
from xml.etree import ElementTree as ET
from xml.parsers import expat
from urllib.parse import urlencode
from .models import Bibliography, clean_text, find_year, normalize_doi, safe_url

ATOM = "{http://www.w3.org/2005/Atom}"
PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
DCTERMS = "{http://purl.org/dc/terms/}"

ENDPOINTS = {
    "jstage": "https://api.jstage.jst.go.jp/searchapi/do",
    "cinii": "https://cir.nii.ac.jp/opensearch/v2/articles",
    "ndl": "https://ndlsearch.ndl.go.jp/api/opensearch",
}


def request_url(provider: str, query: str, count: int, cinii_app_id: str | None = None,
                *, start: int = 1, ndl_provider: str = "", newest: bool = False) -> str:
    if not clean_text(query):
        raise ValueError("検索語は必須です")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 100:
        raise ValueError("1回あたりの取得件数は1〜100です")
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise ValueError("開始位置は正の整数です")
    if provider == "jstage":
        # 論文タイトルの部分一致検索。本文の取得は行わない。
        params = {"service": "3", "article": query, "start": str(start), "count": count}
    elif provider == "cinii":
        if not clean_text(cinii_app_id):
            raise ValueError("CiNii Research は CINII_APP_ID が必要です")
        if start + count - 1 > 10000:
            raise ValueError("CiNiiの取得範囲は10000件以内です")
        params = {"appid": cinii_app_id.strip(), "q": query, "format": "atom", "lang": "ja", "count": count, "start": start, "sortorder": 0 if newest else 4}
    elif provider == "ndl":
        if start + count - 1 > 500:
            raise ValueError("NDL OpenSearchの取得範囲は500件以内です")
        params = {"any": query, "cnt": count, "idx": start}
        if ndl_provider:
            if ndl_provider not in {"zassaku", "zassaku-online", "iss-ndl-opac", "open"}:
                raise ValueError("利用条件を確認済みのNDL提供元だけ指定できます")
            params["dpid"] = ndl_provider
    else:
        raise ValueError(f"未対応の収集元: {provider}")
    return ENDPOINTS[provider] + "?" + urlencode(params)


def _parse_xml(payload: bytes) -> ET.Element:
    """DTDを展開する前に拒否し、安全な文書だけを要素木へ変換する。

    バイト列の文字検索ではUTF-16の宣言を見逃すため、Expatの宣言ハンドラ
    で検査する。Expat未対応のUTF-32等は構文エラーとして閉じて拒否する。
    外部DTD・外部実体は読み込まない。
    """
    parser = expat.ParserCreate()

    def reject_declaration(*_args):
        raise ValueError("DTD・実体宣言を含むXMLは受け付けません")

    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_declaration
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise ET.ParseError("XMLの形式が不正です") from exc
    return ET.fromstring(payload)


def _require_root(root: ET.Element, tag: str, provider: str) -> None:
    if root.tag != tag:
        raise ValueError(f"{provider}の応答形式が不正です")


def _check_empty_response(node: ET.Element, entries: list[ET.Element], provider: str) -> None:
    """HTMLやAPIエラーを、検索結果ゼロ件として隠さない。"""
    for child in node:
        if child.tag.split("}")[-1].lower() in ("error", "errors", "diagnostics"):
            raise ValueError(f"{provider}からエラー応答が返されました")
    if not entries and _rss_text(node, "totalResults") != "0":
        raise ValueError(f"{provider}の応答に検索結果情報がありません")


def _missing_record(provider: str) -> None:
    warnings.warn(f"{provider}: タイトルまたは元IDがない書誌をスキップしました", UserWarning, stacklevel=3)


def _text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    found = node.find(tag)
    return clean_text("".join(found.itertext())) if found is not None else ""


def _text_lang(node: ET.Element, container: str) -> str:
    parent = node.find(ATOM + container)
    return (_text(parent, ATOM + "ja") or _text(parent, ATOM + "en")) if parent is not None else ""


def _pages(node: ET.Element) -> str:
    page_range = _text(node, PRISM + "pageRange")
    if page_range:
        return page_range
    first = _text(node, PRISM + "startingPage")
    last = _text(node, PRISM + "endingPage")
    return f"{first}–{last}" if first and last and first != last else (first or last)


def _rss_text(node: ET.Element, local_name: str) -> str:
    for child in node:
        if child.tag.split("}")[-1] == local_name:
            return clean_text("".join(child.itertext()))
    return ""


def _rss_texts(node: ET.Element, local_name: str) -> list[str]:
    return [clean_text("".join(ch.itertext())) for ch in node if ch.tag.split("}")[-1] == local_name and clean_text("".join(ch.itertext()))]


def _doi_from_identifiers(node: ET.Element) -> str | None:
    for el in node.iter():
        if el.tag.split("}")[-1] in ("identifier", "doi"):
            found = normalize_doi("".join(el.itertext()))
            if found:
                return found
    return None


def parse_jstage(payload: bytes) -> list[Bibliography]:
    root = _parse_xml(payload)
    _require_root(root, ATOM + "feed", "J-STAGE")
    result = root.find(ATOM + "result")
    status = _text(result, ATOM + "status")
    if not status:
        raise ValueError("J-STAGEの応答に処理結果ステータスがありません")
    if status == "ERR_001":
        return []
    if status == "WARN_002":
        warnings.warn("J-STAGE: 取得件数の上限に達しました（WARN_002）", UserWarning, stacklevel=2)
    elif status != "0":
        raise ValueError("J-STAGE API エラー: " + (_text(result, ATOM + "message") or status))
    entries = root.findall(ATOM + "entry")
    _check_empty_response(root, entries, "J-STAGE")
    records = []
    for entry in entries:
        title = _text_lang(entry, "article_title") or _text(entry, ATOM + "title")
        author_group = entry.find(ATOM + "author")
        lang = None
        if author_group is not None:
            lang = author_group.find(ATOM + "ja")
            if lang is None or not lang.findall(ATOM + "name"):
                lang = author_group.find(ATOM + "en")
        authors = tuple(clean_text("".join(x.itertext())) for x in lang.findall(ATOM + "name")) if lang is not None else ()
        link = _text_lang(entry, "article_link")
        if not link:
            link_el = entry.find(ATOM + "link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
        record_id = _text(entry, ATOM + "id") or safe_url(link) or _text(entry, ATOM + "joi")
        link = safe_url(link) or safe_url(record_id)
        doi = _text(entry, PRISM + "doi")
        record_id = record_id or normalize_doi(doi)
        publication = _text_lang(entry, "material_title")
        if not title or not record_id:
            _missing_record("J-STAGE")
            continue
        records.append(Bibliography(
            provider="jstage", provider_id=record_id,
            title=title, authors=authors, year=find_year(_text(entry, ATOM + "pubyear")),
            publication=publication, doi=doi, url=link, material_type="論文", pages=_pages(entry),
        ).validated())
    return records


def parse_cinii(payload: bytes) -> list[Bibliography]:
    root = _parse_xml(payload)
    _require_root(root, ATOM + "feed", "CiNii Research")
    entries = root.findall(ATOM + "entry")
    _check_empty_response(root, entries, "CiNii Research")
    records = []
    for entry in entries:
        title = _text(entry, ATOM + "title")
        record_id = _text(entry, ATOM + "id")
        link = ""
        for node in entry.findall(ATOM + "link"):
            href = node.attrib.get("href", "")
            if node.attrib.get("rel", "alternate") == "alternate" and ".rdf" not in href:
                link = href
                break
        link = safe_url(link) or safe_url(record_id)
        record_id = record_id or link
        authors = tuple(_text(x, ATOM + "name") for x in entry.findall(ATOM + "author"))
        authors = tuple(a for a in authors if a)
        if not authors:
            authors = tuple(_rss_texts(entry, "creator"))
        year = find_year(_text(entry, PRISM + "publicationDate") or _text(entry, DC + "date"))
        if not title or not record_id:
            _missing_record("CiNii Research")
            continue
        records.append(Bibliography(
            provider="cinii", provider_id=record_id,
            title=title, authors=authors, year=year,
            publication=_text(entry, PRISM + "publicationName") or _text(entry, DC + "publisher"),
            doi=_doi_from_identifiers(entry), url=link,
            material_type=_text(entry, DC + "type") or "論文", pages=_pages(entry),
        ).validated())
    return records


def parse_ndl(payload: bytes) -> list[Bibliography]:
    root = _parse_xml(payload)
    _require_root(root, "rss", "NDLサーチ")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("NDLサーチの応答にchannelがありません")
    items = channel.findall("item")
    _check_empty_response(channel, items, "NDLサーチ")
    records = []
    for item in items:
        title = _rss_text(item, "title")
        link = _rss_text(item, "link")
        authors = tuple(_rss_texts(item, "creator") or _rss_texts(item, "author"))
        # RSSのpubDateはメタデータ更新日時の場合があり、出版年には使わない。
        date = _text(item, DCTERMS + "issued") or _rss_text(item, "date")
        identifier = next((safe_url(x) for x in _rss_texts(item, "identifier") if safe_url(x)), "")
        record_id = _rss_text(item, "guid") or safe_url(link) or identifier
        if not title or not record_id:
            _missing_record("NDLサーチ")
            continue
        institutions = []
        for child in item:
            if child.tag.split("}")[-1] == "dataProvider":
                name = clean_text("".join(child.itertext()))
                uri = safe_url(child.attrib.get(RDF + "resource", ""))
                value = f"{name} ({uri})" if name and uri else name or uri
                if value and value not in institutions:
                    institutions.append(value)
        records.append(Bibliography(
            provider="ndl", provider_id=record_id,
            title=title, authors=authors, year=find_year(date),
            publication=_rss_text(item, "publisher") or _rss_text(item, "source"),
            doi=_doi_from_identifiers(item), url=safe_url(link) or safe_url(record_id) or identifier,
            material_type=_rss_text(item, "type") or _rss_text(item, "category") or "図書・史料等",
            source_institution="; ".join(institutions),
        ).validated())
    return records


PARSERS = {"jstage": parse_jstage, "cinii": parse_cinii, "ndl": parse_ndl}
