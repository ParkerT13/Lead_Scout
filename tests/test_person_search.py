import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_ps_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import engine, site_harvester as sh
from emailer.person_search import find_personal_email
from emailer.pattern_detector import Detection


def fake_search(results):
    def search(q, max_results=10):
        return results
    return search


class PersonSearchTests(unittest.TestCase):
    def test_finds_published_address_for_that_person(self):
        s = fake_search([{"title": "Authors - SPE-123", "href": "https://spe.org/p/1",
                          "body": "Jane Smith (jsmith@acme.com), Bob Ray (bray@acme.com) Acme Energy"}])
        self.assertEqual(find_personal_email("Jane", "Smith", "acme.com", "Acme", search=s, pause=(0, 0)),
                         ("jsmith@acme.com", "https://spe.org/p/1"))

    def test_ignores_other_people_other_domains_and_role_addresses(self):
        s = fake_search([{"title": "x", "href": "u",
                          "body": "Bob Ray bray@acme.com  info@acme.com  jsmith@gmail.com jsmith@other.com"}])
        self.assertIsNone(find_personal_email("Jane", "Smith", "acme.com", "Acme", search=s, pause=(0, 0)))

    def test_search_errors_never_raise(self):
        def boom(q, max_results=10):
            raise RuntimeError("rate limited")
        self.assertIsNone(find_personal_email("Jane", "Smith", "acme.com", search=boom, pause=(0, 0)))


class EngineIntegrationTests(unittest.TestCase):
    def enrich(self, title, enr):
        det = Detection("flast", "scraped", None, 3, [])
        with mock.patch.object(engine, "find_domain", return_value="acme.com"), \
                mock.patch.object(engine, "get_mx", return_value="mx"), \
                mock.patch.object(engine, "network_up", return_value=True), \
                mock.patch.object(engine, "detect_pattern_ex", return_value=det), \
                mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "cache_put"), mock.patch.object(engine, "put_harvest"), \
                mock.patch.object(engine, "get_harvest", return_value=None), \
                mock.patch.object(engine, "get_kb_entry", return_value=None), \
                mock.patch.object(engine, "get_kb_pattern2", return_value=None), \
                mock.patch.object(engine, "ddg_mail_domain", return_value=""), \
                mock.patch.object(engine, "is_bounced", return_value=False), \
                mock.patch.object(sh, "harvest_ex", return_value=sh.HarvestResult([], {})):
            c = {"name": "Jane Smith", "first_name": "Jane", "last_name": "Smith",
                 "title": title, "company": "Acme"}
            return list(enr.enrich_company("Acme", [c]))[0]

    def test_high_fit_person_gets_searched_and_marked_found(self):
        enr = engine.Enricher(person_search=True)
        with mock.patch("emailer.person_search.find_personal_email",
                        return_value=("jane.smith@acme.com", "https://x")) as f:
            r = self.enrich("Senior Geologist", enr)
        f.assert_called_once()
        self.assertEqual((r["email"], r["email_status"], r["confidence"]),
                         ("jane.smith@acme.com", "found-on-site", "High"))

    def test_low_fit_person_not_searched(self):
        enr = engine.Enricher(person_search=True)
        with mock.patch("emailer.person_search.find_personal_email", side_effect=AssertionError("no")):
            r = self.enrich("Accounts Payable Clerk", enr)
        self.assertEqual(r["email"], "jsmith@acme.com")

    def test_off_by_default(self):
        enr = engine.Enricher()
        with mock.patch("emailer.person_search.find_personal_email", side_effect=AssertionError("no")):
            r = self.enrich("Senior Geologist", enr)
        self.assertEqual(r["email_status"], "pattern-confirmed")

    def test_per_company_cap(self):
        enr = engine.Enricher(person_search=True, person_search_cap=0)
        with mock.patch("emailer.person_search.find_personal_email", side_effect=AssertionError("no")):
            self.enrich("Senior Geologist", enr)


if __name__ == "__main__":
    unittest.main()
