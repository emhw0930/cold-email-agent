# Tests for the MCP company_h1b_lookup matching.
#
#   python -m unittest tests.test_mcp_lookup      (stdlib only — no pytest)
#
# The _match_score tests are pure and always run. The end-to-end test needs the
# committed USCIS DB; it skips itself if the DB or an employer row is missing.

import unittest

from src.mcp.server import _match_score, company_h1b_lookup


class MatchScore(unittest.TestCase):
    def test_within_word_substring_is_not_a_match(self):
        # The regression: "TRUIST" must NOT match "ALTRUIST" (it's only a
        # within-word substring, a different company). This was the original bug.
        self.assertEqual(_match_score("TRUIST", "ALTRUIST"), 0)

    def test_whole_word_subset_matches_strongly(self):
        # "TRUIST" is a whole word inside "TRUIST BANK".
        self.assertGreaterEqual(_match_score("TRUIST", "TRUIST BANK"), 70)

    def test_exact_is_100(self):
        self.assertEqual(_match_score("APPLE", "APPLE"), 100)

    def test_no_shared_word_is_zero(self):
        self.assertEqual(_match_score("APPLE", "MICROSOFT"), 0)

    def test_empty_inputs_are_zero(self):
        self.assertEqual(_match_score("", "APPLE"), 0)
        self.assertEqual(_match_score("APPLE", ""), 0)


class Lookup(unittest.TestCase):
    def _lookup(self, name):
        r = company_h1b_lookup(name)
        if not r.get("found"):
            self.skipTest(f"no DB row for {name!r} — DB absent or name differs")
        return r

    def test_truist_not_conflated_with_altruist(self):
        # Truist Bank alone should be matched; its approvals must not include
        # Altruist's. Altruist is small (~5); Truist Bank is ~121.
        truist = self._lookup("Truist")
        self.assertIn("Truist", truist["matched_name"])
        self.assertGreater(truist["total_approvals"], 50)

        altruist = company_h1b_lookup("Altruist")
        if altruist.get("found"):
            self.assertNotEqual(truist["matched_name"], altruist["matched_name"])
            # The two companies' numbers must be disjoint, not summed together.
            self.assertNotEqual(truist["total_approvals"],
                                altruist["total_approvals"])

    def test_unknown_company_returns_not_found(self):
        r = company_h1b_lookup("Zzzznotacompany Qxqx")
        self.assertFalse(r["found"])


if __name__ == "__main__":
    unittest.main()
