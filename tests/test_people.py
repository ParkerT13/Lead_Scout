import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.people import ContactBook, make_contact, name_key, person_key, is_linkedin
from scraper.sources.teampages import extract_people

CARDS = """
<html><body><nav><a href="/team">Our Team</a><a href="/contact">Contact Us</a></nav>
<h1>Our Leadership</h1>
<div class="member"><h3>John Smith</h3><p>Chief Executive Officer</p></div>
<div class="member"><h4><a href="/p/2">Maria Garcia-Lopez</a></h4><span class="role">Vice President, Exploration</span></div>
<ul>
  <li>Robert J. Lee - Senior Reservoir Engineer</li>
  <li>Dana White, Land Manager</li>
  <li>Karen Young | Geophysicist</li>
</ul>
<table><tr><td>Tom O'Brien</td><td>Drilling Superintendent</td></tr></table>
<div class="card"><strong>Priya Patel, P.E.</strong><br>Production Engineer</div>
<a href="/news">Read More</a><p>Houston Texas Office</p><p>Meet The Team</p>
<footer><p>Fake Person - Chief Fake Officer</p></footer>
</body></html>
"""


class TeamPageTests(unittest.TestCase):
    def setUp(self):
        self.people = dict(extract_people(CARDS))

    def test_finds_common_layouts(self):
        for name, title_part in (("John Smith", "Chief Executive"), ("Maria Garcia-Lopez", "Exploration"),
                                 ("Robert Lee", "Reservoir"), ("Dana White", "Land"),
                                 ("Karen Young", "Geophysicist"), ("Tom O'Brien", "Drilling"),
                                 ("Priya Patel", "Production")):
            hit = [t for n, t in self.people.items() if n.split()[0] == name.split()[0] and n.split()[-1] == name.split()[-1]]
            self.assertTrue(hit, f"{name} not found in {self.people}")
            self.assertIn(title_part.lower(), hit[0].lower())

    def test_navigation_and_chrome_never_become_people(self):
        for bad in ("Contact Us", "Our Team", "Read More", "Houston Texas", "Meet The", "Our Leadership"):
            self.assertNotIn(bad, self.people)
        self.assertFalse([n for n in self.people if n.startswith("Fake")])     # footer removed

    def test_title_alone_is_not_a_name(self):
        self.assertEqual(extract_people("<div><h3>Senior Geologist</h3><p>Chief Engineer</p></div>"), [])


class ContactBookTests(unittest.TestCase):
    def test_same_person_merges_across_sources_keeping_linkedin(self):
        book = ContactBook()
        a = make_contact("Robert Lee", "Reservoir Engineer", "Ring Energy", "Team page", "https://ring.com/team")
        b = make_contact("Robert B. Lee, PE", "Senior Reservoir Engineer", "Ring Energy", "DDG",
                         linkedin_url="https://www.linkedin.com/in/rlee")
        rec, new = book.add(a)
        self.assertTrue(new)
        rec, new = book.add(b)
        self.assertFalse(new)
        self.assertEqual(len(book), 1)
        self.assertTrue(is_linkedin(rec["linkedin_url"]))
        self.assertEqual(rec["title"], "Senior Reservoir Engineer")
        self.assertEqual(rec["source"], "Team page+DDG")

    def test_different_companies_not_merged(self):
        book = ContactBook()
        book.add(make_contact("Ann Lee", "Geologist", "A Energy", "x", "https://a.com"))
        _, new = book.add(make_contact("Ann Lee", "Geologist", "B Energy", "x", "https://b.com"))
        self.assertTrue(new)

    def test_unusable_names_rejected(self):
        self.assertIsNone(make_contact("Cher", "Singer", "X", "s"))
        self.assertIsNone(make_contact("Trevor P.", "Eng", "X", "s"))

    def test_synthetic_refs_unique_per_person(self):
        a = make_contact("Ann Lee", "G", "X", "s", "https://x.com/team")
        b = make_contact("Bo Ray", "G", "X", "s", "https://x.com/team")
        self.assertNotEqual(a["linkedin_url"], b["linkedin_url"])
        self.assertFalse(is_linkedin(a["linkedin_url"]))

    def test_name_key_ignores_credentials_and_accents(self):
        self.assertEqual(name_key("José Muñoz, PE"), name_key("Jose Munoz"))


if __name__ == "__main__":
    unittest.main()


class CleanupTests(unittest.TestCase):
    def test_letter_spaced_titles_repaired_but_normal_titles_untouched(self):
        from scraper.sources.teampages import _clean_title as c
        self.assertEqual(c("Foun d er & Chief Executi ve"), "Founder & Chief Executive")
        self.assertEqual(c("Director of Operations and VP of Land"), "Director of Operations and VP of Land")
        self.assertEqual(c("Vice President of Geoscience"), "Vice President of Geoscience")

    def test_all_caps_names_titlecased(self):
        names = dict(extract_people("<div><h3>KYLE MILLER</h3><p>Chief Executive Officer</p></div>"))
        self.assertIn("Kyle Miller", names)


class BiographyTests(unittest.TestCase):
    def test_prose_biography_under_name_heading(self):
        html = ("<div><h3>Bobby D. Riley</h3><p>Bobby Riley holds the position of President and Chief Executive "
                "Officer of Riley Permian. In addition, he was appointed Chairman of the Board.</p></div>"
                "<div><h3>Kevin Riley</h3><p>Kevin Riley serves as Chief Operating Officer since 2018, where he leads drilling.</p></div>")
        got = dict(extract_people(html, company="Riley Permian"))
        self.assertEqual(got["Bobby Riley"], "President and Chief Executive Officer")
        self.assertEqual(got["Kevin Riley"], "Chief Operating Officer")
