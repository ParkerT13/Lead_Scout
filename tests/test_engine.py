"""Offline tests for emailer.engine (network and verifiers mocked)."""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import engine, site_harvester as sh
from emailer.pattern_detector import Detection

C = lambda n, f, l: {"name": n, "first_name": f, "last_name": l, "company": "Acme Energy"}


def make(findings=None, pattern="flast", source="scraped", **kw):
    det = Detection(pattern, source, None, 3, findings or [])
    patches = [
        mock.patch.object(engine, "find_domain", return_value="acme.com"),
        mock.patch.object(engine, "get_mx", return_value="mx.acme.com"),
        mock.patch.object(engine, "detect_pattern_ex", return_value=det),
        mock.patch.object(engine, "cache_get", return_value=None),
        mock.patch.object(engine, "cache_put"),
        mock.patch.object(engine, "get_harvest", return_value=None),
        mock.patch.object(engine, "put_harvest"),
        mock.patch.object(engine, "get_kb_pattern2", return_value=None),
        mock.patch.object(engine, "is_bounced", return_value=False),
        mock.patch.object(sh, "harvest_ex", return_value=sh.HarvestResult(list(findings or []), {})),
        mock.patch.object(engine, "ddg_mail_domain", return_value=""),
    ]
    for p in patches:
        p.start()
        self_cleanup.append(p)
    return engine.Enricher(contact_delay=(0, 0), probe_delay=0, **kw)


self_cleanup = []


class EngineTests(unittest.TestCase):
    def tearDown(self):
        while self_cleanup:
            self_cleanup.pop().stop()

    def run_co(self, enr, contacts):
        return list(enr.enrich_company("Acme Energy", contacts))

    def test_found_on_site_beats_guess(self):
        f = [sh.Finding("john.smith@acme.com", "John Smith")]
        r = self.run_co(make(f), [C("John Smith", "John", "Smith")])[0]
        self.assertEqual((r["email"], r["email_status"], r["confidence"]),
                         ("john.smith@acme.com", "found-on-site", "High"))

    def test_no_verifier_labels_evidence(self):
        r = self.run_co(make(), [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertEqual((r["email"], r["email_status"]), ("alee@acme.com", "pattern-confirmed"))
        self.assertEqual(r["confidence"], "Medium")

    def test_weak_pattern_is_labelled_weak(self):
        r = self.run_co(make(pattern="first.last", source="cache"), [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertEqual(r["email_status"], "pattern-ddg")
        r = self.run_co(make(pattern=None, source="none"), [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertEqual(r["email_status"], "best-guess")

    def test_api_verify_and_learn(self):
        enr = make(pattern=None, source="none", nb_key="k")
        calls = []

        def fake_api(email, nb, rk):
            calls.append(email)
            return {"status": "verified" if email.startswith("ann.lee@") else "bounced", "detail": "x"}

        with mock.patch.object(engine, "verify_via_api", side_effect=fake_api):
            rows = self.run_co(enr, [C("Ann Lee", "Ann", "Lee"), C("Bob Ray", "Bob", "Ray")])
        self.assertEqual(rows[0]["email"], "ann.lee@acme.com")
        self.assertEqual(rows[0]["email_status"], "verified")
        # format learned from contact 1 -> contact 2 is tried as first.last immediately
        self.assertEqual(calls[:2], ["alee@acme.com", "ann.lee@acme.com"])
        self.assertEqual(calls[2], "bob.ray@acme.com")
        self.assertTrue(rows[1]["email"].startswith(("bob.ray@", "robert.ray@")))

    def test_catch_all_upgrade_with_reliable_source(self):
        enr = make(nb_key="k")
        with mock.patch.object(engine, "verify_via_api", return_value={"status": "catch-all-risky"}):
            r = self.run_co(enr, [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertEqual(r["email_status"], "catch-all-confirmed")

    def test_inconclusive_smtp_falls_back_to_unverified(self):
        enr = make(port25_ok=True)
        with mock.patch.object(engine, "smtp_verify", return_value={"status": "unknown"}):
            rows = self.run_co(enr, [C(f"A{i} Lee", f"Anna{i}", "Lee") for i in range(4)])
        self.assertTrue(all(r["email_status"] == "pattern-confirmed" for r in rows))
        self.assertFalse(enr.port25_ok)           # auto-disabled after 3 inconclusive

    def test_bounced_candidates_skipped(self):
        enr = make()
        with mock.patch.object(engine, "is_bounced", side_effect=lambda e: e == "alee@acme.com"):
            r = self.run_co(enr, [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertNotEqual(r["email"], "alee@acme.com")

    def test_no_domain(self):
        enr = make()
        with mock.patch.object(engine, "find_domain", return_value=""):
            r = self.run_co(enr, [C("Ann Lee", "Ann", "Lee")])[0]
        self.assertEqual(r["email_status"], "no domain")

    def test_initial_only_last_name_skipped(self):
        r = self.run_co(make(), [C("Trevor P.", "Trevor", "P.")])[0]
        self.assertEqual((r["email"], r["email_status"]), ("", "no candidates"))

    def test_missing_last_name(self):
        r = self.run_co(make(), [C("Cher", "Cher", "")])[0]
        self.assertEqual(r["email_status"], "no candidates")


if __name__ == "__main__":
    unittest.main()


class RcptClassifyTests(unittest.TestCase):
    def test_classify(self):
        from emailer.email_verifier import classify_rcpt as c
        self.assertEqual(c(250, b"OK"), "accepted")
        self.assertEqual(c(550, b"5.7.1 Service unavailable, Client host [1.2.3.4] blocked using Spamhaus"), "unknown")
        self.assertEqual(c(550, b"5.1.1 User unknown"), "rejected")
        self.assertEqual(c(550, b"5.1.10 RESOLVER.ADR.RecipientNotFound"), "rejected")
        self.assertEqual(c(550, b"No such user here"), "rejected")
        self.assertEqual(c(554, b"5.7.1 Recipient address rejected: Access denied"), "unknown")
        self.assertEqual(c(550, b"Access denied"), "unknown")
        self.assertEqual(c(450, b"try later"), "unknown")


class EmailFormatSiteTests(unittest.TestCase):
    def _page(self, pct, label, example):
        from tests.test_site_harvester import _cf
        return (f"<div class='format_score_container'><div class='format'>{label}</div>"
                f"<div class='confidence_value'>{pct}%</div><div class='format_example'>e.g. "
                f"<a data-cfemail='{_cf(example)}'>[email protected]</a></div></div>")

    def run_lookup(self, html):
        from emailer import pattern_detector as pd
        with mock.patch.object(pd.requests, "get", return_value=mock.Mock(status_code=200, text=html)):
            return pd._emailformat_lookup("acme.com")

    def test_parses(self):
        self.assertEqual(self.run_lookup(self._page(74, "first_name  last_name", "jane.doe@acme.com")), "first.last")
        self.assertEqual(self.run_lookup(self._page(80, "first_initial  last_name", "jdoe@acme.com")), "flast")
        self.assertEqual(self.run_lookup(self._page(60, "first_name  last_name", "jane_doe@acme.com")), "first_last")

    def test_low_confidence_ignored(self):
        self.assertIsNone(self.run_lookup(self._page(37, "first_name  last_name", "jane.doe@acme.com")))


class PrefetchTests(unittest.TestCase):
    def test_warms_every_company_once(self):
        enr = engine.Enricher()
        seen = []
        with mock.patch.object(engine, "find_domain", side_effect=lambda c: c.lower() + ".com"), \
                mock.patch.object(engine, "get_mx", return_value="mx"), \
                mock.patch.object(enr, "_detect", side_effect=lambda c, d, say, seed=None: seen.append((c, d))):
            pf = enr.prefetch(["A", "B", "C", "D"])
            for c in ("A", "B", "C", "D"):
                pf.wait(c, timeout=10)
            pf.close()
        self.assertEqual(sorted(seen), [("A", "a.com"), ("B", "b.com"), ("C", "c.com"), ("D", "d.com")])

    def test_failure_does_not_raise(self):
        enr = engine.Enricher()
        with mock.patch.object(engine, "find_domain", side_effect=RuntimeError("boom")):
            pf = enr.prefetch(["A"])
            pf.wait("A", timeout=10)      # must swallow the error
            pf.close()


class MailDomainTests(unittest.TestCase):
    def test_uses_domain_published_on_site(self):
        enr = engine.Enricher()
        rows = [sh.Finding(f"p{i}@mgyoil.com", "") for i in range(5)]
        res = sh.HarvestResult([], {"mgyoil.com": rows, "vendor.com": rows[:1]})
        mx = lambda d: "mx" if d == "mgyoil.com" else None
        with mock.patch.object(engine, "find_domain", return_value="magnoliaoilgas.com"),                 mock.patch.object(engine, "get_mx", side_effect=mx),                 mock.patch.object(engine, "ddg_mail_domain", return_value=""),                 mock.patch.object(sh, "harvest_ex", return_value=res):
            domain, seed = enr._resolve_domain("Magnolia", lambda m: None)
        self.assertEqual(domain, "mgyoil.com")
        self.assertEqual(len(seed), 5)

    def test_unrelated_single_address_not_trusted(self):
        enr = engine.Enricher()
        res = sh.HarvestResult([], {"agency.com": [sh.Finding("ir@agency.com")]})
        with mock.patch.object(engine, "find_domain", return_value="acme.com"),                 mock.patch.object(engine, "get_mx", side_effect=lambda d: "mx" if d == "agency.com" else None),                 mock.patch.object(engine, "ddg_mail_domain", return_value=""),                 mock.patch.object(sh, "harvest_ex", return_value=res):
            domain, seed = enr._resolve_domain("Acme", lambda m: None)
        self.assertEqual(domain, "acme.com")      # stays; later reported as "no MX"
        self.assertEqual(seed, [])


class HygieneTests(unittest.TestCase):
    def test_job_title_not_a_company(self):
        from emailer.domain_finder import looks_like_job_title as j
        for t in ("STAFF ENGINEER", "Region Manager", "VP Engineering", "Petroleum Engineer", "Senior Geologist"):
            self.assertTrue(j(t), t)
        for t in ("Ring Energy", "Chord Energy", "Engineering Partners LLC", "Hilcorp", "Staff Oil Co"):
            self.assertFalse(j(t), t)

    def test_name_suffix_and_initials(self):
        n = engine.name_parts
        self.assertEqual(n({"name": "Lawrence Eyman III", "first_name": "Lawrence", "last_name": "III"})[:2], ("Lawrence", "Eyman"))
        self.assertEqual(n({"name": "Emery Petrof, III", "first_name": "Emery", "last_name": "III"})[:2], ("Emery", "Petrof"))
        self.assertEqual(n({"name": "Ann Lee", "first_name": "Ann", "last_name": "Lee"})[:2], ("Ann", "Lee"))
        self.assertEqual(n({"name": "Cher"})[:2], ("Cher", ""))
        self.assertEqual(n({"name": "TzuFang (Janice) Chiu"})[:2], ("TzuFang", "Chiu"))

    def test_domain_validation(self):
        from emailer import domain_finder as df
        page = lambda t: mock.Mock(text=t + (" filler" * 400), status_code=200)
        with mock.patch("requests.get", return_value=page("verdad real estate development partners")):
            self.assertFalse(df._site_mentions_company("verdad.com", "Verdad Resources"))
        with mock.patch("requests.get", return_value=page("Verdad is an independent oil and gas operator")):
            self.assertTrue(df._site_mentions_company("x.com", "Verdad Resources"))
        with mock.patch("requests.get", return_value=page("This domain is for sale. oil gas verdad")):
            self.assertFalse(df._site_mentions_company("x.com", "Verdad Resources"))
        with mock.patch("requests.get", return_value=mock.Mock(text="", status_code=403)):
            self.assertIsNone(df._site_check("x.com", "Verdad Resources"))

    def test_find_domain_rejects_wrong_company(self):
        from emailer import domain_finder as df
        with mock.patch.object(df, "_resolve", side_effect=lambda d: d), \
                mock.patch.object(df, "_site_check", return_value=False), \
                mock.patch("emailer.domain_cache.get", return_value=None), \
                mock.patch.dict("sys.modules", {"ddgs": mock.Mock(DDGS=mock.MagicMock(side_effect=RuntimeError("x")))}):
            self.assertEqual(df.find_domain("Verdad Resources"), "")
        self.assertEqual(df.find_domain("REGION MANAGER"), "")

    def test_redirecting_guess_follows_to_real_site(self):
        from emailer import domain_finder as df
        checks = {"scoutep.com": True, "scoutenergypartners.com": None}
        with mock.patch.object(df, "_resolve", side_effect=lambda d: "scoutep.com"), \
                mock.patch.object(df, "_site_check", side_effect=lambda d, c: checks[d]), \
                mock.patch("emailer.domain_cache.get", return_value=None):
            self.assertEqual(df.find_domain("Scout Energy Partners"), "scoutep.com")


class MailDomainPreferenceTests(unittest.TestCase):
    def resolve(self, own_findings, foreign, mx):
        enr = engine.Enricher()
        res = sh.HarvestResult(own_findings, foreign)
        with mock.patch.object(engine, "find_domain", return_value="scoutenergypartners.com"), \
                mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "get_mx", side_effect=lambda d: "mx" if d in mx else None), \
                mock.patch.object(engine, "get_kb_entry", return_value=None), \
                mock.patch.object(engine, "ddg_mail_domain", return_value=""), \
                mock.patch.object(sh, "harvest_ex", return_value=res):
            return enr._resolve_domain("Scout", lambda m: None)

    def rows(self, n, dom="scoutep.com"):
        return [sh.Finding(f"p{i}@{dom}") for i in range(n)]

    def test_strong_published_domain_beats_silent_web_domain(self):
        domain, seed = self.resolve([], {"scoutep.com": self.rows(5)}, {"scoutenergypartners.com", "scoutep.com"})
        self.assertEqual(domain, "scoutep.com")

    def test_web_domain_kept_when_it_publishes_its_own(self):
        own = [sh.Finding("a@scoutenergypartners.com")]
        domain, seed = self.resolve(own, {"scoutep.com": self.rows(5)}, {"scoutenergypartners.com", "scoutep.com"})
        self.assertEqual(domain, "scoutenergypartners.com")
        self.assertEqual(seed, own)

    def test_weak_foreign_evidence_ignored(self):
        domain, _ = self.resolve([], {"scoutep.com": self.rows(2)}, {"scoutenergypartners.com", "scoutep.com"})
        self.assertEqual(domain, "scoutenergypartners.com")


class RegistrableTests(unittest.TestCase):
    def test_registrable(self):
        from emailer.domain_finder import registrable as r
        self.assertEqual(r("corporate.exxonmobil.com"), "exxonmobil.com")
        self.assertEqual(r("www.foo.co.uk"), "foo.co.uk")
        self.assertEqual(r("ringenergy.com"), "ringenergy.com")


class LabelEvidenceTests(unittest.TestCase):
    def test_js_site_with_name_and_industry_in_domain(self):
        from emailer import domain_finder as df
        with mock.patch("requests.get", return_value=mock.Mock(text="<script>x</script>", status_code=200)):
            self.assertTrue(df._site_mentions_company("silverhillenergy.com", "Silver Hill Energy Partners"))
            self.assertIsNone(df._site_check("verdad.com", "Verdad Resources"))   # name only: unreadable -> unknown


class CompanyMailDomainTests(unittest.TestCase):
    def test_search_supplies_mail_domain_when_site_silent(self):
        enr = engine.Enricher()
        with mock.patch.object(engine, "find_domain", return_value="scoutenergypartners.com"), \
                mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "get_kb_entry", return_value=None), \
                mock.patch.object(engine, "get_mx", return_value="mx"), \
                mock.patch.object(engine, "ddg_mail_domain", return_value="scoutep.com"), \
                mock.patch.object(sh, "harvest_ex", return_value=sh.HarvestResult([], {})):
            self.assertEqual(enr._resolve_domain("Scout Energy Partners", lambda m: None), ("scoutep.com", None))

    def test_not_asked_when_site_has_addresses(self):
        enr = engine.Enricher()
        with mock.patch.object(engine, "find_domain", return_value="acme.com"), \
                mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "get_kb_entry", return_value=None), \
                mock.patch.object(engine, "get_mx", return_value="mx"), \
                mock.patch.object(engine, "ddg_mail_domain", side_effect=AssertionError("should not search")), \
                mock.patch.object(sh, "harvest_ex", return_value=sh.HarvestResult([sh.Finding("a@acme.com")], {})):
            self.assertEqual(enr._resolve_domain("Acme", lambda m: None)[0], "acme.com")
