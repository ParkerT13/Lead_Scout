"""Offline tests for emailer.site_harvester (no network). Run: py -3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import site_harvester as sh
from emailer.generator import generate_candidates

TEAM = """
<html><body>
<div class="member"><h3>John Smith</h3><p>VP Geoscience</p>
  <a href="mailto:jsmith@acme.com">Email</a></div>
<div class="member"><h3>Maria Garcia</h3><p>Land Manager</p>
  <a href="mailto:mgarcia@acme.com?subject=Hi">Email Maria</a></div>
<div class="member"><h3>Robert Lee</h3><p>CFO</p>
  <span data-cfemail="%s">[email protected]</span></div>
<p>Reach Dana White at dwhite [at] acme [dot] com</p>
<a href="mailto:info@acme.com">info@acme.com</a>
<a href="mailto:other@elsewhere.org">partner</a>
<script type="application/ld+json">{"@type":"Person","name":"Karen Young","email":"mailto:kyoung@acme.com"}</script>
</body></html>
"""


def _cf(email, key=0x42):
    return "%02x" % key + "".join("%02x" % (ord(c) ^ key) for c in email)


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.f = {x.email: x for x in sh.extract_from_html(TEAM % _cf("rlee@acme.com"), "acme.com")}

    def test_all_layers_found(self):
        for e in ("jsmith@acme.com", "mgarcia@acme.com", "rlee@acme.com",
                  "dwhite@acme.com", "kyoung@acme.com", "info@acme.com"):
            self.assertIn(e, self.f)

    def test_foreign_domain_excluded(self):
        self.assertNotIn("other@elsewhere.org", self.f)

    def test_names_attached(self):
        self.assertEqual(self.f["jsmith@acme.com"].name, "John Smith")
        self.assertEqual(self.f["mgarcia@acme.com"].name, "Maria Garcia")
        self.assertEqual(self.f["kyoung@acme.com"].name, "Karen Young")

    def test_cf_decode(self):
        self.assertEqual(sh.decode_cf_email(_cf("a.b@x.com")), "a.b@x.com")


class InferenceTests(unittest.TestCase):
    def test_flast_proven(self):
        r = sh.infer_patterns(sh.extract_from_html(TEAM % _cf("rlee@acme.com"), "acme.com"))
        self.assertEqual(r.pattern, "flast")
        self.assertTrue(r.strong)
        self.assertGreaterEqual(r.support, 3)

    def test_second_pattern(self):
        f = [sh.Finding("jsmith@a.com", "John Smith"), sh.Finding("mgarcia@a.com", "Maria Garcia"),
             sh.Finding("tom.ng@a.com", "Tom Ng"), sh.Finding("ann.ho@a.com", "Ann Ho")]
        r = sh.infer_patterns(f)
        self.assertEqual({r.pattern, r.pattern2}, {"flast", "first.last"})

    def test_role_addresses_ignored(self):
        self.assertIsNone(sh.infer_patterns([sh.Finding("info@a.com"), sh.Finding("sales@a.com")]).pattern)

    def test_dot_hint_without_name_is_weak(self):
        r = sh.infer_patterns([sh.Finding("jane.doe@a.com")])
        self.assertEqual(r.pattern, "first.last")
        self.assertFalse(r.strong)

    def test_wrong_name_not_counted(self):
        r = sh.infer_patterns([sh.Finding("jsmith@a.com", "Contact Us")])
        self.assertIsNone(r.pattern)


class ContactMatchTests(unittest.TestCase):
    F = [sh.Finding("michael.brown@a.com", "Michael Brown"), sh.Finding("jsmith@a.com", "John Smith"),
         sh.Finding("smith@a.com", "")]

    def test_exact(self):
        self.assertEqual(sh.find_contact_email(self.F, "John", "Smith", "a.com"), "jsmith@a.com")

    def test_nickname(self):
        self.assertEqual(sh.find_contact_email(self.F, "Mike", "Brown", "a.com"), "michael.brown@a.com")

    def test_weak_single_token_needs_name(self):
        f = [sh.Finding("smith@a.com", "")]
        self.assertIsNone(sh.find_contact_email(f, "Zed", "Smith", "a.com"))

    def test_no_match(self):
        self.assertIsNone(sh.find_contact_email(self.F, "Ann", "Jones", "a.com"))


class GeneratorTests(unittest.TestCase):
    def test_nickname_alt_candidate(self):
        c = generate_candidates("Mike", "Brown", "a.com", "first.last")
        self.assertEqual(c[0], "mike.brown@a.com")
        self.assertEqual(c[1], "michael.brown@a.com")

    def test_no_pattern_unchanged(self):
        c = generate_candidates("John", "Smith", "a.com")
        self.assertEqual(c[0], "jsmith@a.com")
        self.assertEqual(len(c), 8)


if __name__ == "__main__":
    unittest.main()
