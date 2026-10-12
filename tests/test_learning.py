import csv, os, sys, tempfile, unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="ls_learn_")
os.environ["LEADSCOUT_OUTPUT_DIR"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import learning, domain_cache
from emailer.generator import generate_candidates


def write_contacts():
    rows = [("Ann", "Lee"), ("Bo", "Ray"), ("Cy", "Poe"), ("Di", "Fox")]
    with open(Path(TMP) / "pipeline_test.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["first_name", "last_name", "email"])
        for i, (fn, ln) in enumerate(rows):
            w.writerow([fn, ln, f"{fn[0]}{ln}@acme.com".lower() if i < 3 else f"{fn}.{ln}@acme.com".lower()])


class LearningTests(unittest.TestCase):
    def setUp(self):
        for name in ("format_outcomes.json", "domain_cache.json", "bounces.json"):
            p = Path(TMP) / name
            if p.exists():
                p.unlink()
        write_contacts()

    def test_bounces_demote_the_failing_format_and_cache_is_corrected(self):
        domain_cache.put("Acme", "acme.com", "flast", "scraped")
        s = learning.record_outcomes(["alee@acme.com", "bray@acme.com", "cpoe@acme.com"])
        self.assertEqual(s["bounced_known"], 3)
        self.assertEqual(learning.bad_patterns("acme.com"), {"flast"})
        notes = learning.apply_to_cache()
        self.assertEqual(len(notes), 1)
        entry = domain_cache.get("Acme")
        self.assertEqual(entry["pattern"], "first.last")           # next most likely format
        self.assertEqual(entry["pattern_source"], "outcome-guess")

    def test_deliveries_confirm_a_format(self):
        domain_cache.put("Acme", "acme.com", "flast", "hubspot")
        learning.record_outcomes([], ["di.fox@acme.com"] * 1)
        learning.record_outcomes([], ["di.fox@acme.com"])          # two delivered first.last addresses
        notes = learning.apply_to_cache()
        self.assertTrue(notes)
        self.assertEqual(domain_cache.get("Acme")["pattern"], "first.last")
        self.assertEqual(domain_cache.get("Acme")["pattern_source"], "outcome-verified")

    def test_manual_format_is_never_overridden(self):
        domain_cache.put("Acme", "acme.com", "flast", "manual")
        learning.record_outcomes(["alee@acme.com", "bray@acme.com", "cpoe@acme.com"])
        self.assertEqual(learning.apply_to_cache(), [])
        self.assertEqual(domain_cache.get("Acme")["pattern"], "flast")

    def test_one_bounce_is_not_enough(self):
        learning.record_outcomes(["alee@acme.com"])
        self.assertEqual(learning.bad_patterns("acme.com"), set())

    def test_unknown_people_are_counted_but_ignored(self):
        s = learning.record_outcomes(["stranger@acme.com"])
        self.assertEqual((s["bounced_known"], s["bounced_unknown"]), (0, 1))

    def test_candidates_put_failing_format_last(self):
        c = generate_candidates("Ann", "Lee", "acme.com", "flast", avoid={"flast"})
        self.assertNotEqual(c[0], "alee@acme.com")
        self.assertEqual(c[-1], "alee@acme.com")
        self.assertEqual(len(c), 8)

    def test_end_to_end_from_files(self):
        b = Path(TMP) / "bounces.csv"
        b.write_text("Email Address\nalee@acme.com\nbray@acme.com\ncpoe@acme.com\n", encoding="utf-8")
        sent = Path(TMP) / "sent.txt"
        sent.write_text("alee@acme.com\nbray@acme.com\ncpoe@acme.com\ndi.fox@acme.com\n", encoding="utf-8")
        domain_cache.put("Acme", "acme.com", "flast", "scraped")
        summary = learning.learn_from_files(b, sent)
        self.assertEqual((summary["bounced"], summary["delivered"]), (3, 1))
        self.assertTrue(summary["changes"])


if __name__ == "__main__":
    unittest.main()
