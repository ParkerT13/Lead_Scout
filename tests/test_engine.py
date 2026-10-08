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
        r = self.run_co(make(pattern="first.last", source="ddg"), [C("Ann Lee", "Ann", "Lee")])[0]
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

    def test_missing_last_name(self):
        r = self.run_co(make(), [C("Cher", "Cher", "")])[0]
        self.assertEqual(r["email_status"], "no candidates")


if __name__ == "__main__":
    unittest.main()


class EmailFormatTests(unittest.TestCase):
    def test_emailformat_parse(self):
        from emailer import pattern_detector as pd
        resp = mock.Mock(status_code=200, text="<p>74% {first}.{last}@acme.com</p>")
        with mock.patch.object(pd.requests, "get", return_value=resp):
            self.assertEqual(pd._emailformat_lookup("acme.com"), "first.last")


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
