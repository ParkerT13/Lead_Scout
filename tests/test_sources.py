import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.sources import sec

FORM4 = """<?xml version="1.0"?><ownershipDocument>
<issuer><issuerName>Ring Energy</issuerName></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>1</rptOwnerCik><rptOwnerName>MCKINNEY PAUL D</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO and Chairman</officerTitle></reportingOwnerRelationship></reportingOwner>
<reportingOwner><reportingOwnerId><rptOwnerName>Acme Capital Partners LP</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship></reportingOwner>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE JR</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
</ownershipDocument>"""


class SecTests(unittest.TestCase):
    def test_parse_form4(self):
        people = sec.parse_form4(FORM4)
        self.assertEqual(people, [{"name": "Paul McKinney", "title": "CEO and Chairman"},
                                  {"name": "Jane Doe", "title": "Director"}])

    def test_entities_and_garbage_skipped(self):
        self.assertEqual(sec.parse_form4("<not xml"), [])

    def test_company_name_matching(self):
        sec._tickers_cache = {sec._norm("Black Stone Minerals, L.P."): 1589150,
                              sec._norm("Chord Energy Corp"): 1486159,
                              sec._norm("EXXON MOBIL CORP"): 34088}
        self.assertEqual(sec.find_cik("Black Stone Minerals"), "0001589150")
        self.assertEqual(sec.find_cik("chord energy"), "0001486159")
        self.assertEqual(sec.find_cik("exxonmobil"), "0000034088")
        self.assertIsNone(sec.find_cik("Hilcorp"))
        sec._tickers_cache = None

    def test_discover_end_to_end_with_mocked_http(self):
        sec._tickers_cache = {sec._norm("Ring Energy Inc"): 1384195}
        subs = mock.Mock(status_code=200)
        subs.json.return_value = {"filings": {"recent": {"form": ["8-K", "4"], "accessionNumber": ["0001-24-1", "0001-24-2"],
                                                         "primaryDocument": ["a.htm", "xslF345X05/wk-form4.xml"]}}}
        xml = mock.Mock(status_code=200, text=FORM4)
        calls = []

        def fake_get(self, url, **kw):
            calls.append(url)
            return subs if "submissions" in url else xml
        with mock.patch("requests.Session.get", fake_get), mock.patch("scraper.sources.sec.time.sleep"):
            cs = sec.discover("Ring Energy")
        sec._tickers_cache = None
        self.assertEqual([c["name"] for c in cs], ["Paul McKinney", "Jane Doe"])
        self.assertEqual(cs[0]["source"], "SEC filings")
        self.assertTrue(any(u.endswith("/wk-form4.xml") for u in calls))     # XSL folder stripped


if __name__ == "__main__":
    unittest.main()


class WebMentionTests(unittest.TestCase):
    V = ["Ring Energy"]

    def parse(self, text):
        from scraper.sources.web import parse_mentions
        return parse_mentions(text, self.V)

    def test_common_phrasings(self):
        self.assertIn(("Jane Smith", "Senior Geologist"),
                      self.parse("Presented by Jane Smith, Senior Geologist at Ring Energy, Midland"))
        self.assertIn(("Tom Lee", "VP Exploration"), self.parse("Tom Lee - VP Exploration - Ring Energy"))
        self.assertIn(("Ann Ray", "Reservoir Engineer"), self.parse("Ring Energy Reservoir Engineer Ann Ray said"))

    def test_junk_rejected(self):
        self.assertEqual(self.parse("Ring Energy announces results. The Company reported record production at Ring Energy"), [])
        self.assertEqual(self.parse("Oil Gas, Senior Geologist at Ring Energy"), [])

    def test_other_company_ignored(self):
        self.assertEqual(self.parse("Jane Smith, Senior Geologist at Chord Energy"), [])
