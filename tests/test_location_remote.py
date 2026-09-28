"""Regression tests: a remote segment that names a foreign place does not endorse a req.

"Remote - Colombia | Chicago" must not pass the location gate as remote on Colombia's word when the
only US option is Chicago onsite, outside the home market. Country-scoped remote outside the US fails
the location policy, so the Colombia segment lends nothing and the req is LOCATION-POLICY.

Offline."""
import unittest

from tests.stubs import sweep, gates

G = gates()


def verdict(loc):
    r = sweep.norm("T", "greenhouse", 1, "Senior Product Manager", loc, "https://example.test/T/1",
                   "2026-09-18", "5+ years of experience.")
    return sweep.gate(r, G)[0]


class ForeignRemoteSegment(unittest.TestCase):
    def test_foreign_remote_beside_unendorsed_us_city_is_location_policy(self):
        self.assertEqual(verdict("Remote - Colombia | Chicago"), "LOCATION-POLICY")

    def test_foreign_remote_beside_endorsed_city_still_passes_on_the_city(self):
        self.assertEqual(verdict("Remote - Colombia | Denver, CO"), "PASS")

    def test_bare_region_and_us_remote_still_endorse(self):
        for loc in ("Remote | Chicago", "Remote - US | Chicago", "Remote (North America) | Austin, TX",
                    "Remote/Hybrid | Chicago, IL"):
            self.assertEqual(verdict(loc), "PASS", loc)

    def test_an_endorsed_foreign_market_keeps_its_path(self):
        self.assertEqual(verdict("Calgary, AB - Remote | Jersey City, NJ"), "PASS")

    def test_the_helper(self):
        self.assertTrue(sweep.remote_names_foreign_place("Remote - Colombia", G))
        self.assertFalse(sweep.remote_names_foreign_place("Remote - US", G))
        self.assertFalse(sweep.remote_names_foreign_place("Remote-Friendly (Travel-Required)", G))


if __name__ == "__main__":
    unittest.main()
