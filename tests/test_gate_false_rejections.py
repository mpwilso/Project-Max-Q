"""Regression tests: false rejections in the location, years and title gates, found by sampling failed
postings from a real sweep, with the correct FAILs beside each fix that must stay FAILs.

Root causes: comma- and '&'-joined countries in one location segment; endorsed cities and US twins of
foreign cities not read as American; an exact segment written with a ZIP, street or work-style word;
remote synonyms ('Anywhere', 'Virtual', North America); the body check running only for a bare
'Remote'; preferred headings missed, and preferred-only years gating TENURE; dual-level postings; the
'in absence of a degree' and 'University Degree' tiers; a company's history 'for 30 years'; and title
substrings ('warehouse', 'backend').

Run against the example candidate's gates.json (Denver metro onsite, or remote in the US). Offline."""
import unittest

from tests.stubs import sweep, gates

G = gates()


def gate(loc, title="Senior Product Manager", desc="5+ years of experience.", company="T", g=G):
    r = sweep.norm(company, "greenhouse", 1, title, loc, "https://example.test/T/1", "2026-10-10", desc)
    return sweep.gate_final(r, g)


def verdict(loc, **kw):
    return gate(loc, **kw)[0]


class CommaAndJoinedCountries(unittest.TestCase):
    def test_us_remote_beside_canada_remote_passes(self):
        # The Canada half used to sink the US half.
        self.assertEqual(verdict("Canada - Remote, US - Remote"), "PASS")
        self.assertEqual(verdict("Remote in the US, Remote in Canada"), "PASS")
        self.assertEqual(verdict("United States & Canada | Remote"), "PASS")

    def test_canada_only_still_fails(self):
        for loc in ("Remote - Canada", "Canada - Remote", "Remote, Canada", "Canada - Remote, UK - Remote",
                    "Canada & Mexico | Remote"):
            self.assertEqual(verdict(loc), "FAIL", loc)

    def test_the_split(self):
        self.assertEqual(sweep._loc_segments("Canada - Remote, US - Remote"), ["Canada - Remote", "US - Remote"])
        self.assertEqual(sweep._loc_segments("San Francisco, CA"), ["San Francisco, CA"])
        self.assertEqual(sweep._loc_segments("Remote, Canada"), ["Remote, Canada"])
        self.assertEqual(sweep._loc_segments("United States & Canada"), ["United States", "Canada"])


class EndorsedCitiesAndTwins(unittest.TestCase):
    def test_an_endorsed_city_is_american(self):
        # A suburb in endorsed_onsite_markets, written with no state, used to fail closed.
        for loc in ("GREENWOOD VILLAGE CAMPUS", "Broomfield Office", "Littleton"):
            self.assertEqual(verdict(loc), "PASS", loc)

    def test_sf_is_a_us_city(self):
        self.assertEqual(verdict("SF Office | Remote"), "PASS")
        self.assertEqual(verdict("SF"), "LOCATION-POLICY")              # American, not an endorsed market

    def test_us_twin_of_a_foreign_city(self):
        self.assertEqual(verdict("Dublin, CA | Remote"), "PASS")
        self.assertEqual(verdict("Dublin, CA"), "LOCATION-POLICY")
        self.assertEqual(verdict("London, KY"), "LOCATION-POLICY")

    def test_foreign_cities_still_fail(self):
        for loc in ("Dublin", "Dublin, Ireland", "Toronto, CA", "London, ON, CA", "Dublin, ie | Remote",
                    "Paris, France"):
            self.assertEqual(verdict(loc), "FAIL", loc)


def exact_gates():
    """The example gates with New York endorsed as an exact segment instead of blocked."""
    g = dict(G)
    ny = {"new york", "new york city", "nyc", "manhattan", "brooklyn", "queens", "bronx", "hybrid - new york"}
    g["location_fail_any"] = [x for x in G["location_fail_any"] if x not in ny]
    g["blocked_us_markets"] = [x for x in G["blocked_us_markets"] if x not in ny]
    g["endorsed_exact_segments"] = ["new york", "new york, us"]
    g["endorsed_exact_zip_prefixes"] = {"new york": ["100", "101", "102", "103", "104"]}
    return g


class ExactSegments(unittest.TestCase):
    G2 = exact_gates()

    def test_the_segment_with_zip_street_or_work_style(self):
        for loc in ("NY New York 30 Hudson Yards", "New York, US, 10003", "New York - Hybrid", "New York (Hybrid)",
                    "Hybrid - New York", "New York Office", "USA - New York", "NY 10001", "New York, New York"):
            self.assertEqual(verdict(loc, g=self.G2), "PASS", loc)

    def test_other_places_in_the_state_still_need_approval(self):
        for loc in ("New York - Albany", "New York - Tarrytown", "Tarrytown, NY 10591", "Albany, NY 12207",
                    "Austin, TX", "New York State"):
            self.assertEqual(verdict(loc, g=self.G2), "LOCATION-POLICY", loc)

    def test_no_exact_segments_means_no_match(self):
        self.assertFalse(sweep._exact_segment("New York, US, 10003", [], G))
        self.assertEqual(verdict("New York - Hybrid"), "FAIL")         # the example candidate blocks it


class RemoteSynonyms(unittest.TestCase):
    def test_anywhere_virtual_and_north_america(self):
        for loc in ("U.S. Anywhere", "Anywhere, US", "US FL JAX 347 | US Anywhere", "PA - Virtual - C+",
                    "Virtual Office (Florida)", "North America | Remote", "Remote (North America)",
                    "Remote - North America"):
            self.assertEqual(verdict(loc), "PASS", loc)

    def test_foreign_remote_still_fails(self):
        for loc in ("Anywhere in Canada", "Virtual - UK", "Canada - Remote | North America", "Remote - Germany",
                    "Remote - Colombia"):
            self.assertEqual(verdict(loc), "FAIL", loc)


class BodyCheck(unittest.TestCase):
    def test_body_decides_when_the_list_names_no_place(self):
        cases = (("Remote | Remote | Remote", "East Coast US location preferred."),
                 ("Remote, Global | Remote", "We hire globally. You can work from anywhere in the world."),
                 ("Remote/Road/Home Office", "Working Conditions: Stamford or Hartford, Connecticut"),
                 ("Remote | Remote", "Open to candidates across North America."),
                 ("Virtual", "The pay range in Colorado is $120,000 - $150,000."))
        for loc, desc in cases:
            v, why = gate(loc, desc=desc)
            self.assertEqual(v, "PASS", loc)
            self.assertTrue(any(w.startswith("VERIFY REMOTE") for w in why), (loc, why))

    def test_no_remote_option_is_location_policy_not_fail(self):
        for loc, desc in (("Eastern or Central Timezone", "The national pay range for this role is $95,800 - $125,000."),
                          ("BLANK,BLANK,Multiple Locations", "California Base Pay Range: $150,000 - $190,000.")):
            v, why = gate(loc, desc=desc)
            self.assertEqual(v, "LOCATION-POLICY", loc)
            self.assertTrue(any(w.startswith("VERIFY REMOTE") for w in why), (loc, why))

    def test_bare_remote_beside_foreign_places_needs_an_explicit_statement(self):
        self.assertEqual(verdict("Remote | Canada | Tokyo",
                                 desc="The role is based in the US, and we're open to strong candidates globally."), "PASS")
        # Ashby appends "Remote" to every remote posting: "Singapore | Remote" is remote in Singapore, and a
        # USD band or the word US in boilerplate does not make it American.
        for loc in ("Singapore | Remote", "Argentina | Argentina | Remote", "AU: Sydney (45 Clarence St) | Remote"):
            self.assertEqual(verdict(loc, desc="Pay: $150,000 - $200,000. We sell to US banks."), "FAIL", loc)

    def test_a_named_foreign_place_never_gets_a_vote(self):
        for loc in ("Madrid Office", "Abu Dhabi, United Arab Emirates", "Ho Chi Minh City, Vietnam", "Stockholm HQ"):
            self.assertEqual(verdict(loc, desc="Offices in Colorado and Texas. $120,000 - $150,000 USD."), "FAIL", loc)

    def test_shouted_us_and_no_body(self):
        self.assertEqual(verdict("Remote", desc="ABOUT US\nWe build tools. JOIN US today."), "FAIL")
        self.assertEqual(verdict("Virtual", desc=""), "FAIL")


TWO_HEADINGS = ("Required/Minimum Qualifications\nBachelor's Degree AND 8+ years experience in product management OR "
                "equivalent experience.\nPreferred/Additional Qualifications\nBachelor's Degree AND 12+ years experience "
                "in product management.")
REQUIRED_COLON = ("Qualifications\nRequired:\nBachelor's Degree AND 6+ years experience in program management.\n"
                  "Preferred:\nBachelor's Degree AND 12+ years experience in program management.")
PREFERRED_ONLY = ("Minimum Qualifications\nDeep experience in software development project management.\n"
                  "Preferred Qualifications\n10 + years of proven experience in software development project management.")


class PreferredYears(unittest.TestCase):
    def test_the_required_bar_wins(self):
        # "OR equivalent experience" is an escape clause, so the label says so.
        self.assertEqual(gate("Denver, CO", desc=TWO_HEADINGS), ("PASS", ["REACH: 8+ years stated with escape clause"]))
        self.assertEqual(gate("Denver, CO", title="Business Program Manager", desc=REQUIRED_COLON),
                         ("PASS", ["REACH: 6+ years stated"]))

    def test_preferred_only_figure_is_reach_never_tenure(self):
        self.assertEqual(gate("Denver, CO", title="Engineering Program Manager", desc=PREFERRED_ONLY),
                         ("PASS", ["REACH: 10+ years stated (preferred qualifications only)"]))
        for desc in ("Qualifications Required: a bachelor's degree. Qualifications We Prefer: 12+ years of program management.",
                     "Basic Qualifications: BA/BS. Additional Qualifications: 10+ years of product management."):
            self.assertEqual(verdict("Denver, CO", desc=desc), "PASS", desc)

    def test_required_over_eight_is_still_tenure(self):
        for desc in ("12+ years of product management required.",
                     "Required qualifications: 10+ years of program management. Preferred qualifications: 12+ years.",
                     "Required:\n9+ years of product management.\nPreferred:\n12+ years of product management."):
            self.assertEqual(verdict("Denver, CO", desc=desc), "TENURE", desc)


class DualLevelDegreeAndHistory(unittest.TestCase):
    def test_dual_level_uses_the_lower_level(self):
        d = ("What you should have:\n8-10+ years of program/project delivery experience for Engagement Delivery "
             "Manager, or 10-12+ years for Senior Engagement Delivery Manager.")
        self.assertEqual(gate("Remote - US", title="Engagement Delivery Manager / Senior Manager", desc=d),
                         ("PASS", ["REACH: 8+ years stated"]))
        d = ("Basic Qualifications- Senior Product Manager\n6+ years experience in Product Management.\n"
             "Basic Qualifications-Principal Product Manager\n10+ years experience in Product Management.")
        self.assertEqual(gate("Denver, CO", title="Principal, Product Manager", desc=d),
                         ("PASS", ["REACH: 6+ years stated"]))

    def test_a_single_level_tied_figure_still_counts(self):
        d = "10+ years of product management experience for a Senior Product Manager."
        self.assertEqual(verdict("Denver, CO", desc=d), "TENURE")

    def test_degree_tiers(self):
        d = ("typically requires a university degree and minimum 8 years prior relevant experience or an advanced "
             "degree in a related field and minimum 5 years of experience or in absence of a degree, 12 years of "
             "relevant experience.")
        self.assertEqual(max(sweep.experience_years(d, G)), 8)

    def test_company_history_for_n_years_is_not_a_bar(self):
        d = "Mortgage servicing has operators who have been doing it one way for 30 years."
        self.assertEqual(verdict("Denver, CO | Remote", title="Product Manager", desc=d), "PASS")
        self.assertEqual(sweep.experience_years(d.lower(), G), [])

    def test_a_candidate_for_n_years_is_still_a_bar(self):
        for d in ("You have worked in product management for 10+ years.",
                  "Worked in product management for 10+ years.",
                  "We need someone who has 10+ years of experience in product management."):
            self.assertEqual(verdict("Denver, CO", desc=d), "TENURE", d)


class TitleSubstrings(unittest.TestCase):
    def test_product_and_program_titles_survive_engineering_words(self):
        for title in ("Group Product Manager, AI-Native Data Warehouse",
                      "Business Program Manager - Capacity Automation & Backend Tools",
                      "Product Manager, Frontend Platform"):
            self.assertEqual(verdict("USA | Remote", title=title), "PASS", title)

    def test_engineering_and_physical_ops_titles_still_fail(self):
        for title in ("Senior Backend Engineer", "Staff Software Engineer, Backend", "Full Stack Developer",
                      "Product Manager, Warehouse Management Systems", "Program Manager, Data Center Construction",
                      "SDE II, Program Manager Tools"):
            self.assertEqual(verdict("USA | Remote", title=title), "FAIL", title)


class AnyWordMatchesEveryTerm(unittest.TestCase):
    def test_the_alternation_agrees_with_the_per_term_check(self):
        terms = G["location_fail_any"] + G["us_cities"]
        for s in ("dublin, ca", "remote - canada", "new york city", "the uk office", "ukraine", "denver",
                  "san francisco bay", "pune", "costa rica remote", ""):
            self.assertEqual(sweep._has_any_word(s, terms), any(sweep._has_word(s, t) for t in terms), s)
        self.assertFalse(sweep._has_any_word("anything", []))


if __name__ == "__main__":
    unittest.main()
