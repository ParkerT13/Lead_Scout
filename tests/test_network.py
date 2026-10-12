"""Outage handling: a dropped connection must never be recorded as 'no domain' / 'no MX'."""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_net_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dns.resolver
from emailer import engine, smtp_verifier as sv
from emailer.pattern_detector import Detection

C = lambda n, f, l: {"name": n, "first_name": f, "last_name": l, "company": "Acme"}


class NetworkTests(unittest.TestCase):
    def test_outage_gives_retryable_error_not_no_domain(self):
        enr = engine.Enricher(should_stop=lambda: True)
        with mock.patch.object(engine, "network_up", return_value=False):
            rows = list(enr.enrich_company("Acme", [C("Ann Lee", "Ann", "Lee")]))
        self.assertEqual(rows[0]["email_status"], "error")
        self.assertIn("internet", rows[0]["email_source"])

    def test_false_no_domain_during_outage_is_retried(self):
        enr = engine.Enricher()
        calls = {"n": 0}

        def resolve(company, say):
            calls["n"] += 1
            return ("", None) if calls["n"] == 1 else ("acme.com", [])

        net = iter([True, False, True, True, True, True])      # up, drops, back
        with mock.patch.object(enr, "_resolve_domain", side_effect=resolve), \
                mock.patch.object(engine, "network_up", side_effect=lambda: next(net, True)), \
                mock.patch.object(engine, "get_mx", return_value="mx"), \
                mock.patch.object(engine, "detect_pattern_ex",
                                  return_value=Detection("flast", "scraped", None, 3, [])), \
                mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "cache_put"), mock.patch.object(engine, "put_harvest"), \
                mock.patch.object(engine, "get_harvest", return_value=None), \
                mock.patch.object(engine, "get_kb_pattern2", return_value=None), \
                mock.patch.object(engine, "is_bounced", return_value=False), \
                mock.patch("emailer.engine.time.sleep"):
            rows = list(enr.enrich_company("Acme", [C("Ann Lee", "Ann", "Lee")]))
        self.assertEqual(rows[0]["email"], "alee@acme.com")
        self.assertEqual(calls["n"], 2)

    def test_mx_failures_not_cached_definitive_answers_are(self):
        sv._MX_CACHE.clear()
        with mock.patch.object(sv.dns.resolver, "resolve", side_effect=dns.resolver.LifetimeTimeout()):
            self.assertIsNone(sv.get_mx("flaky.example"))
        self.assertNotIn("flaky.example", sv._MX_CACHE)
        with mock.patch.object(sv.dns.resolver, "resolve", side_effect=dns.resolver.NXDOMAIN()):
            self.assertIsNone(sv.get_mx("gone.example"))
        self.assertIn("gone.example", sv._MX_CACHE)

    def test_empty_crawl_not_cached(self):
        enr = engine.Enricher()
        with mock.patch.object(engine, "cache_get", return_value=None), \
                mock.patch.object(engine, "get_harvest", return_value=None), \
                mock.patch.object(engine, "cache_put"), \
                mock.patch.object(engine, "get_kb_pattern2", return_value=None), \
                mock.patch.object(engine, "detect_pattern_ex", return_value=Detection(None, "none", None, 0, [])), \
                mock.patch.object(engine, "put_harvest") as put:
            enr._detect("Acme", "acme.com", lambda m: None, seed=[])
        put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
