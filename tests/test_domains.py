import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_dom_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import domain_finder as df


class GuessTests(unittest.TestCase):
    def test_suffix_only_stripped_as_a_separate_word(self):
        self.assertEqual(df._build_guesses("hilcorp"), ["hilcorp.com"])          # was "hil.com"
        self.assertEqual(df._build_guesses("Zinc Oil Inc"), ["zincoil.com"])
        self.assertEqual(df._build_guesses("Ring Energy, Inc.")[0], "ringenergy.com")
        self.assertEqual(df._build_guesses("Cabral Energy LLC")[0], "cabralenergy.com")
        self.assertEqual(df._build_guesses("Chord Energy"), ["chordenergy.com", "chord.com"])


class CrmKnownDomainTests(unittest.TestCase):
    def test_domain_with_real_crm_contacts_accepted_despite_unreadable_homepage(self):
        with mock.patch.object(df, "_resolve", side_effect=lambda d: d), \
                mock.patch.object(df, "_site_check", return_value=False), \
                mock.patch.object(df, "_in_crm_kb", side_effect=lambda d: d == "hilcorp.com"), \
                mock.patch("emailer.domain_cache.get", return_value=None):
            self.assertEqual(df.find_domain("Hilcorp"), "hilcorp.com")

    def test_unknown_domain_still_needs_page_evidence(self):
        with mock.patch.object(df, "_resolve", side_effect=lambda d: d), \
                mock.patch.object(df, "_site_check", return_value=False), \
                mock.patch.object(df, "_in_crm_kb", return_value=False), \
                mock.patch("emailer.domain_cache.get", return_value=None), \
                mock.patch.dict("sys.modules", {"ddgs": mock.Mock(DDGS=mock.MagicMock(side_effect=RuntimeError("x")))}):
            self.assertEqual(df.find_domain("Verdad Resources"), "")


class ResolveTests(unittest.TestCase):
    def test_get_fallback_when_head_is_reset(self):
        import requests
        ok = mock.Mock(url="https://crescentenergy.com/")
        with mock.patch.object(requests, "head", side_effect=requests.ConnectionError("reset")), \
                mock.patch.object(requests, "get", return_value=ok):
            self.assertEqual(df._resolve("crescentenergy.com"), "crescentenergy.com")


if __name__ == "__main__":
    unittest.main()
