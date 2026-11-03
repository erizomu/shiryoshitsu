import unittest
from xml.etree.ElementTree import ParseError
from shiryoshitsu.providers import parse_jstage, parse_cinii, parse_ndl, request_url
from shiryoshitsu.models import normalize_doi, identity_text, safe_url
from urllib.parse import urlparse, parse_qs

# 以下のテスト用書誌はすべて架空のデータであり、実在する研究論文ではない。
JSTAGE = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
 xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <result><status>0</status><message/></result>
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry><article_title><ja>鄧艾の政策研究</ja><en>Policy of Deng Ai</en></article_title>
  <id>https://www.jstage.jst.go.jp/article/example/1/1/1/_article/-char/ja/</id>
  <article_link><ja>https://www.jstage.jst.go.jp/article/example/_article/ja</ja></article_link>
  <author><ja><name>研究 太郎</name><name>研究 花子</name></ja><en><name>架空の英文著者名</name></en></author>
  <material_title><ja>架空の歴史学雑誌</ja></material_title>
  <prism:startingPage>12</prism:startingPage><prism:endingPage>25</prism:endingPage>
  <prism:doi>10.1234/EXAMPLE.1</prism:doi><pubyear>2024</pubyear><updated>2026-11-01T00:00:00+09:00</updated>
  </entry></feed>'''.encode()
CINII = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
 <opensearch:totalResults>1</opensearch:totalResults>
 <entry><id>https://cir.nii.ac.jp/crid/999999999</id><title>鄧艾の政策研究</title>
 <link href="https://cir.nii.ac.jp/crid/999999999.rdf" rel="related" />
 <link href="https://cir.nii.ac.jp/crid/999999999" rel="alternate" />
 <author><name>研究 太郎</name></author><prism:publicationName>架空の歴史学雑誌</prism:publicationName>
 <prism:publicationDate>2024-03-01</prism:publicationDate>
 <dc:identifier rdf:datatype="https://cir.nii.ac.jp/schema/1.0/doi">10.1234/example.1</dc:identifier>
 <prism:pageRange>12-25</prism:pageRange>
 </entry></feed>'''.encode()
NDL = '''<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"
 xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:openSearch="http://a9.com/-/spec/opensearchrss/1.0/">
<channel><openSearch:totalResults>1</openSearch:totalResults>
<item><title>史料目録の整理</title><link>https://ndlsearch.ndl.go.jp/books/example</link>
<guid isPermaLink="true">https://ndlsearch.ndl.go.jp/books/R100000002-I000000001</guid>
<dc:creator>研究 花子</dc:creator><dcterms:issued>2016</dcterms:issued>
<pubDate>Tue, 03 Nov 2026 09:00:00 +0900</pubDate>
<dcndl:dataProvider>架空の資料館</dcndl:dataProvider>
<dc:publisher>架空出版</dc:publisher><dc:type>図書</dc:type></item>
</channel></rss>'''.encode()


class ParserTests(unittest.TestCase):
    def test_jstage(self):
        row, = parse_jstage(JSTAGE)
        self.assertEqual((row.title, row.year, row.pages), ("鄧艾の政策研究", 2024, "12–25"))
        self.assertEqual(row.authors, ("研究 太郎", "研究 花子"))
        self.assertEqual(row.doi, "10.1234/example.1")
        self.assertIn("www.jstage.jst.go.jp", row.url)
        self.assertEqual(row.provider_id, "https://www.jstage.jst.go.jp/article/example/1/1/1/_article/-char/ja/")

    def test_cinii(self):
        row, = parse_cinii(CINII)
        self.assertEqual(row.year, 2024)
        self.assertEqual(row.doi, "10.1234/example.1")
        self.assertEqual(row.authors, ("研究 太郎",))
        self.assertEqual(row.pages, "12-25")
        self.assertEqual(row.provider_id, "https://cir.nii.ac.jp/crid/999999999")
        self.assertEqual(row.url, "https://cir.nii.ac.jp/crid/999999999")

    def test_ndl(self):
        row, = parse_ndl(NDL)
        self.assertEqual(row.title, "史料目録の整理")
        self.assertEqual(row.material_type, "図書")
        self.assertEqual(row.year, 2016)
        self.assertEqual(row.source_institution, "架空の資料館")
        self.assertEqual(row.provider_id, "https://ndlsearch.ndl.go.jp/books/R100000002-I000000001")

    def test_jstage_empty_and_warning(self):
        empty = '<feed xmlns="http://www.w3.org/2005/Atom"><result><status>ERR_001</status><message>ERR_001</message></result></feed>'
        self.assertEqual(parse_jstage(empty.encode()), [])
        with self.assertWarnsRegex(UserWarning, "WARN_002"):
            self.assertEqual(len(parse_jstage(JSTAGE.replace(b"<status>0</status>", b"<status>WARN_002</status>"))), 1)

    def test_jstage_error_statuses(self):
        for status in ("ERR_003", "ERR_004", "SYS_ERR_009"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, status):
                parse_jstage(JSTAGE.replace(b"<status>0</status>", f"<status>{status}</status>".encode()))

    def test_zero_results(self):
        atom = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>0</opensearch:totalResults></feed>'
        rss = '<rss version="2.0" xmlns:opensearch="http://a9.com/-/spec/opensearchrss/1.0/"><channel><opensearch:totalResults>0</opensearch:totalResults></channel></rss>'
        self.assertEqual(parse_cinii(atom.encode()), [])
        self.assertEqual(parse_ndl(rss.encode()), [])
        jstage = atom.replace("<opensearch:totalResults>", "<result><status>0</status></result><opensearch:totalResults>")
        self.assertEqual(parse_jstage(jstage.encode()), [])

    def test_wrong_responses_are_not_empty_searches(self):
        for parser in (parse_jstage, parse_cinii, parse_ndl):
            for payload in (b"<html><body>API error</body></html>", b"<error>API error</error>"):
                with self.subTest(parser=parser.__name__, payload=payload), self.assertRaises(ValueError):
                    parser(payload)
        for parser, payload in (
            (parse_cinii, b'<feed xmlns="http://www.w3.org/2005/Atom"><error>API error</error></feed>'),
            (parse_cinii, b'<feed xmlns="http://www.w3.org/2005/Atom"/>'),
            (parse_jstage, b'<feed xmlns="http://www.w3.org/2005/Atom"/>'),
            (parse_ndl, b'<rss><channel><error>API error</error></channel></rss>'),
            (parse_ndl, b'<rss><channel/></rss>'),
            (parse_ndl, b'<rss/>'),
        ):
            with self.subTest(parser=parser.__name__, payload=payload), self.assertRaises(ValueError):
                parser(payload)

    def test_missing_stable_id_is_not_replaced_with_title(self):
        atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>同名の論文</title></entry></feed>'
        jstage = atom.replace("<entry>", "<result><status>0</status></result><entry>")
        rss = '<rss><channel><item><title>同名の史料</title></item></channel></rss>'
        for parser, payload in ((parse_jstage, jstage), (parse_cinii, atom), (parse_ndl, rss)):
            with self.subTest(parser=parser.__name__), self.assertWarnsRegex(UserWarning, "元ID"):
                self.assertEqual(parser(payload.encode()), [])

    def test_jstage_english_fallback_and_id_url(self):
        payload = JSTAGE.decode().replace('<ja>鄧艾の政策研究</ja>', '').replace('<ja><name>研究 太郎</name><name>研究 花子</name></ja>', '')
        payload = payload.replace('<article_link><ja>https://www.jstage.jst.go.jp/article/example/_article/ja</ja></article_link>', '')
        row, = parse_jstage(payload.encode())
        self.assertEqual(row.title, "Policy of Deng Ai")
        self.assertEqual(row.authors, ("架空の英文著者名",))
        self.assertEqual(row.url, row.provider_id)

    def test_ndl_update_date_is_not_publication_date(self):
        row, = parse_ndl(NDL.replace(b"<dcterms:issued>2016</dcterms:issued>", b""))
        self.assertIsNone(row.year)

    def test_ndl_guid_and_institution_resource(self):
        payload = NDL.decode().replace('<link>https://ndlsearch.ndl.go.jp/books/example</link>', '')
        payload = payload.replace('<dcndl:dataProvider>架空の資料館</dcndl:dataProvider>', '<dcndl:dataProvider rdf:resource="https://example.invalid/archive"/>')
        row, = parse_ndl(payload.encode())
        self.assertEqual(row.url, row.provider_id)
        self.assertEqual(row.source_institution, "https://example.invalid/archive")

    def test_cinii_dc_creator_fallback(self):
        payload = CINII.decode().replace('<author><name>研究 太郎</name></author>', '<dc:creator>研究 太郎</dc:creator>')
        row, = parse_cinii(payload.encode())
        self.assertEqual(row.authors, ("研究 太郎",))

    def test_dtd_and_entities_are_rejected_in_multiple_encodings(self):
        # 外部実体・内部実体とも、参照の有無にかかわらず宣言時点で拒否する。
        declarations = (
            '<!DOCTYPE feed SYSTEM "file:///秘密情報">',
            '<!DOCTYPE feed [<!ENTITY secret SYSTEM "https://example.invalid/secret">]>',
            '<!DOCTYPE feed [<!ENTITY a "大量展開"><!ENTITY b "&a;&a;&a;">]>',
        )
        for parser in (parse_jstage, parse_cinii, parse_ndl):
            for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
                for declaration in declarations:
                    payload = f'<?xml version="1.0" encoding="{encoding}"?>{declaration}<feed/>'
                    with self.subTest(parser=parser.__name__, encoding=encoding, declaration=declaration):
                        with self.assertRaises((ValueError, ParseError)):
                            parser(payload.encode(encoding))

    def test_normal_xml_entities_and_utf16(self):
        payload = CINII.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').replace('鄧艾の政策研究', '鄧艾 &amp; 姜維')
        row, = parse_cinii(payload.encode("utf-16"))
        self.assertEqual(row.title, "鄧艾 & 姜維")

    def test_jstage_error(self):
        with self.assertRaisesRegex(ValueError, "検索条件エラー"):
            parse_jstage('<feed xmlns="http://www.w3.org/2005/Atom"><result><status>1</status><message>検索条件エラー</message></result></feed>'.encode())

    def test_malformed(self):
        with self.assertRaises(ParseError):
            parse_cinii(b"not an XML document")

    def test_urls_parameters(self):
        p = parse_qs(urlparse(request_url("jstage", "姜維", 5)).query)
        self.assertEqual(p["service"], ["3"])
        self.assertEqual(p["article"], ["姜維"])
        p = parse_qs(urlparse(request_url("ndl", "松下之綱", 5)).query)
        self.assertEqual(p["any"], ["松下之綱"])
        self.assertEqual(p["cnt"], ["5"])
        with self.assertRaisesRegex(ValueError, "CINII_APP_ID"):
            request_url("cinii", "姜維", 5)
        url = request_url("cinii", "姜維", 5, "test-secret")
        self.assertIn("/opensearch/v2/articles", url)
        self.assertEqual(parse_qs(urlparse(url).query)["appid"], ["test-secret"])
        self.assertEqual(parse_qs(urlparse(url).query)["format"], ["atom"])
        self.assertEqual(parse_qs(urlparse(url).query)["lang"], ["ja"])
        with self.assertRaisesRegex(ValueError, "CINII_APP_ID"):
            request_url("cinii", "姜維", 5, "  ")

    def test_invalid_request_parameters(self):
        for count in (0, -1, 101, 1.5, True, "5"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                request_url("ndl", "史料", count)
        with self.assertRaisesRegex(ValueError, "検索語"):
            request_url("ndl", "  ", 5)
        with self.assertRaisesRegex(ValueError, "未対応"):
            request_url("unknown", "史料", 5)

    def test_normalize_doi_and_url(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1234/TEST.5."), "10.1234/test.5.")
        self.assertNotEqual(identity_text(" 魏 晋 ・ 史 "), identity_text("魏晋史"))
        self.assertEqual(safe_url("javascript:alert(1)"), "")
        self.assertEqual(safe_url("https://someone:secret@site.test/x"), "")
