"""Regression tests: a detail call that leaves the description empty is a failed read, not a fetch.

Some detail readers returned the row untouched when the page had no JobPosting block, and others wrote
"" when the payload was empty. gate_all counted each as fetched, so the row stayed PASS with no body
(years, contract and people-management checks never ran) and the >10% detail-failed health warning
could not fire. The check is central, in gate_all, so every adapter in DETAIL (plugins included) is
held to it.

Offline."""
import types
import unittest

from tests.stubs import sweep, gates

G = gates()
TITLE = "Product Manager, Finance Systems"
LOC = "Remote, United States"
BODY = "Own the NetSuite and Coupa roadmap for the finance systems team. 5+ years."


def detail_row(ats, jid=1, **extra):
    r = sweep.norm("TestCo", ats, jid, TITLE, LOC, f"https://example.test/TestCo/{jid}", None, "")
    r["extra"].update(extra)
    r["_target"] = {"company": "TestCo", "ats": ats, "tenant": "x", "shard": "wd1", "site": "S",
                    "base": "https://example.test", "domain": "example.test"}
    return r


class Patch:
    """Set attributes on sweep for one test and put them back."""

    def patch(self, **kw):
        for k, v in kw.items():
            had, old = hasattr(sweep, k), getattr(sweep, k, None)
            setattr(sweep, k, v)
            self.addCleanup(lambda k=k, had=had, old=old: setattr(sweep, k, old) if had else delattr(sweep, k))


class BuiltInAdaptersLeaveNoSilentEmpty(Patch, unittest.TestCase):
    def assert_failed_read(self, r):
        self.assertEqual(sweep.gate(r, G)[0], "PASS")          # gate-passing before the fetch
        v, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual((stats["fetched"], stats["failed"]), (0, 1))
        verdict, why = v[r["key"]]
        self.assertEqual(verdict, "UNCOVERED")                 # never PASS with no body
        self.assertTrue(any("body not read" in x for x in why), why)
        self.assertNotIn("_detail_on", r)                      # so reuse_detail cannot pick it up
        return v, stats

    def test_workday_empty_jobpostinginfo(self):
        # The CXS call answered 200 with no jobPostingInfo.
        self.patch(get=lambda url, headers=None, **kw: {"jobPostingInfo": {}})
        self.assert_failed_read(detail_row("workday", externalPath="/job/x/R-0001234"))

    def test_radancy_page_without_jobposting(self):
        page = '<html><script type="application/ld+json">{"@type": "Organization"}</script></html>'
        self.patch(get_text=lambda url, headers=None, **kw: page)
        self.assert_failed_read(detail_row("radancy"))

    def test_metacareers_page_without_jobposting(self):
        resp = types.SimpleNamespace(text="<html>no ld+json here</html>", raise_for_status=lambda: None)
        sess = types.SimpleNamespace(get=lambda url, headers=None, timeout=None: resp)
        self.patch(_meta_session=lambda: sess, DELAY=0)
        self.assert_failed_read(detail_row("metacareers"))

    def test_pcsx_empty_job_description(self):
        # position_details answered with no jobDescription.
        self.patch(_pcsx_get=lambda url, params=None, **kw: {})
        self.assert_failed_read(detail_row("pcsx"))

    def test_a_real_body_still_counts_as_fetched(self):
        self.patch(get=lambda url, headers=None, **kw: {"jobPostingInfo": {
            "jobDescription": f"<p>{BODY}</p>", "startDate": "2026-10-09", "location": "Remote, United States"}})
        r = detail_row("workday", externalPath="/job/x/1")
        v, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual((stats["fetched"], stats["failed"]), (1, 0))
        self.assertEqual(v[r["key"]][0], "PASS")
        self.assertEqual(r["_detail_on"], sweep.TODAY.isoformat())


class PluginAdaptersAreHeldToItToo(Patch, unittest.TestCase):
    def setUp(self):
        self._saved = sweep.DETAIL.get("fake_detail")
        self.addCleanup(lambda: sweep.DETAIL.pop("fake_detail", None) if self._saved is None
                        else sweep.DETAIL.__setitem__("fake_detail", self._saved))

    def test_a_plugin_that_returns_the_row_untouched_is_a_failure(self):
        sweep.DETAIL["fake_detail"] = lambda t, row: row
        r = detail_row("fake_detail")
        v, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual((stats["fetched"], stats["failed"]), (0, 1))
        self.assertEqual(v[r["key"]][0], "UNCOVERED")
        self.assertIn("detail fetch failed: " + sweep.EMPTY_DETAIL, v[r["key"]][1])

    def test_whitespace_is_not_a_body(self):
        def detail(t, row):
            row["description"] = "  \n "
            return row
        sweep.DETAIL["fake_detail"] = detail
        r = detail_row("fake_detail")
        _, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual(stats["failed"], 1)

    def test_a_req_the_adapter_marked_closed_is_an_answer(self):
        # taleo_tbe: a closed page has no JobPosting by design and the adapter says so.
        def detail(t, row):
            row["extra"]["closed"] = True
            return row
        sweep.DETAIL["fake_detail"] = detail
        r = detail_row("fake_detail")
        _, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual((stats["fetched"], stats["failed"]), (1, 0))

    def test_an_empty_read_falls_back_to_the_stored_body(self):
        # Same path as an exception: the previous snapshot's text is inherited and the row re-gated.
        sweep.DETAIL["fake_detail"] = lambda t, row: row
        r = detail_row("fake_detail")
        prev = {r["key"]: dict(r, description=BODY)}
        v, stats = sweep.gate_all({r["key"]: r}, prev, G)
        self.assertEqual((stats["failed"], stats["inherited"]), (1, 1))
        self.assertEqual(r["description"], BODY)
        self.assertEqual(v[r["key"]][0], "PASS")

    def test_an_empty_rescue_read_is_stamped_like_a_failed_one(self):
        sweep.DETAIL["fake_detail"] = lambda t, row: row
        r = detail_row("fake_detail")
        r["title"] = "Marathon Producer"
        r["_list_sig"] = sweep.list_signature(r)
        v, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual((stats["rescue_failed"], stats["failed"]), (1, 0))
        self.assertTrue(v[r["key"]][1][0].startswith("title out of lane: rescue fetch failed"))
        self.assertIn("body not read", v[r["key"]][1][0])
        self.assertTrue(r["_rescue"]["failed"])

    def test_empty_reads_reach_the_detail_failed_health_warning(self):
        sweep.DETAIL["fake_detail"] = lambda t, row: row
        rows = {}
        for i in range(12):
            r = detail_row("fake_detail", jid=i)
            rows[r["key"]] = r
        v, stats = sweep.gate_all(rows, {}, G)
        self.assertEqual((stats["fetched"], stats["failed"]), (0, 12))
        h = sweep.sweep_health(rows, v, [], [], {}, {}, stats, 1)
        self.assertTrue(any(w.startswith("detail pass failed on 12 of 12 fetches") for w in h["warnings"]),
                        h["warnings"])


if __name__ == "__main__":
    unittest.main()
