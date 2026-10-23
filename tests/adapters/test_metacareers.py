"""Offline tests for adapters/metacareers.py against invented responses in the board's shape.

The reader keeps its own requests.Session (it must look like Chrome), so the session class is replaced
here by a recorder that answers from fixtures: the jobs page (LSD token and server_revision), the
GraphQL answer with its "for (;;);" prefix (7 invented jobs, two of them repeated as featured), an
empty GraphQL answer, and one job page with a schema.org JobPosting block. Nothing here is a real
posting. The opt-in is forced on for the read tests and left off for the skip test.
"""
import importlib.util, types, unittest

from tests.stubs import ROOT, StubH, fixture, sweep

NAME = "metacareers"
T = {"company": "Meta", "ats": NAME}


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Stands in for requests.Session: records (method, url, merged headers, body), answers by URL."""
    instances = []
    routes = {}

    def __init__(self):
        self.headers, self.calls = {}, []
        FakeSession.instances.append(self)

    def _answer(self, method, url, headers, body):
        self.calls.append((method, url, dict(self.headers, **(headers or {})), body))
        for sub, text in FakeSession.routes.items():
            if sub in url: return FakeResponse(text)
        raise AssertionError(f"FakeSession: no route for {url}")

    def get(self, url, headers=None, timeout=None):
        return self._answer("GET", url, headers, None)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._answer("POST", url, headers, data)


class MetaCase(unittest.TestCase):
    def setUp(self):
        self.m = load()
        FakeSession.instances = []
        FakeSession.routes = {"/graphql": fixture(NAME, "graphql.txt", as_json=False),
                              "/jobs/9000000000001/": fixture(NAME, "job_page.html", as_json=False),
                              "/jobs/": fixture(NAME, "jobs_page.html", as_json=False)}
        self.h = StubH([])
        self.h.requests = types.SimpleNamespace(Session=FakeSession)
        self.h.DELAY = 0
        self.saved = sweep.ALLOW_BROWSER_IMPERSONATION
        sweep.ALLOW_BROWSER_IMPERSONATION = True
        self.addCleanup(setattr, sweep, "ALLOW_BROWSER_IMPERSONATION", self.saved)

    def session(self):
        return FakeSession.instances[0]


class Contract(MetaCase):
    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, [])
        self.assertTrue(callable(self.m.detail))
        self.assertFalse(hasattr(self.m, "lookup"))

    def test_the_plugin_is_what_sweep_loads_for_the_ats_key(self):
        self.assertEqual(sweep.ADAPTERS[NAME]._plugin, "metacareers.py")
        self.assertIn(NAME, sweep.DETAIL)
        self.assertIn(NAME, sweep.ENUMERABLE_ATS)
        self.assertEqual(sweep.PLUGINS[NAME].NAME, NAME)
        self.assertFalse(hasattr(sweep, "metacareers"))       # no copy left in the core file
        self.assertFalse(hasattr(sweep, "_meta_session"))


class OptIn(MetaCase):
    def test_without_the_opt_in_nothing_is_sent_and_the_skip_names_the_flag(self):
        sweep.ALLOW_BROWSER_IMPERSONATION = False
        with self.assertRaises(self.h.OptInRequired) as cm:
            self.m.list_jobs(T, self.h)
        self.assertIn("--allow-browser-impersonation", str(cm.exception))
        self.assertEqual(FakeSession.instances, [])
        self.assertEqual(self.h.robots_checked, [])
        with self.assertRaises(self.h.OptInRequired):
            self.m.detail(T, {"url": "https://www.metacareers.com/jobs/9000000000001/", "extra": {}}, self.h)


class ListRead(MetaCase):
    def setUp(self):
        super().setUp()
        self.rows = self.m.list_jobs(T, self.h)
        self.by = {r["id"]: r for r in self.rows}

    def test_rows_dedupe_featured_against_all_jobs(self):
        self.assertEqual(len(self.rows), 7)                   # 7 + 2 featured repeats -> 7
        self.assertEqual(len({r["key"] for r in self.rows}), 7)
        r = self.by["9000000000001"]
        self.assertEqual(r["key"], "metacareers:Meta:9000000000001")
        self.assertEqual(r["title"], "Technical Program Manager, Finance Systems")
        self.assertEqual(r["location"], "Menlo Park, CA | Remote, US")
        self.assertEqual(r["url"], "https://www.metacareers.com/jobs/9000000000001/")
        self.assertEqual(r["extra"]["teams"], ["Finance"])
        self.assertEqual(r["extra"]["sub_teams"], ["Finance Systems"])
        self.assertIsNone(r["posted"])                        # the list carries no date: detail does
        self.assertEqual(r["description"], "")

    def test_the_bootstrap_then_the_graphql_call(self):
        calls = self.session().calls
        self.assertEqual([(m, u) for m, u, _h, _b in calls],
                         [("GET", self.m.JOBS_PAGE), ("POST", self.m.GRAPHQL)])
        nav = calls[0][2]
        self.assertIn("Chrome", nav["User-Agent"])
        self.assertEqual(nav["Sec-Fetch-Mode"], "navigate")
        post_headers, body = calls[1][2], calls[1][3]
        self.assertEqual(post_headers["X-FB-LSD"], "tok-example-123")
        self.assertEqual(post_headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(body["lsd"], "tok-example-123")
        self.assertEqual(body["doc_id"], self.m._META_DOC_ID)
        self.assertEqual(body["__rev"], "1012345678")

    def test_every_url_went_through_the_robots_check(self):
        self.assertEqual(self.h.robots_checked, [self.m.JOBS_PAGE, self.m.GRAPHQL])

    def test_one_session_per_run(self):
        self.m.list_jobs(T, self.h)
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual([m for m, *_ in self.session().calls], ["GET", "POST", "POST"])

    def test_smoke_stops_at_five(self):
        self.m._meta_sess = None
        self.assertEqual(len(self.m.list_jobs(T, self.h, smoke=True)), 5)


class ListFailures(MetaCase):
    def test_empty_board_raises_and_names_the_doc_id(self):
        FakeSession.routes["/graphql"] = fixture(NAME, "graphql_empty.txt", as_json=False)
        with self.assertRaises(RuntimeError) as cm:
            self.m.list_jobs(T, self.h)
        self.assertIn("doc_id", str(cm.exception))

    def test_a_jobs_page_without_a_token_raises(self):
        FakeSession.routes["/jobs/"] = fixture(NAME, "jobs_page_no_token.html", as_json=False)
        with self.assertRaises(RuntimeError) as cm:
            self.m.list_jobs(T, self.h)
        self.assertIn("LSD token", str(cm.exception))
        self.assertEqual([m for m, *_ in self.session().calls], ["GET"])     # no GraphQL call without it

    def test_a_payload_without_the_prefix_still_parses(self):
        FakeSession.routes["/graphql"] = fixture(NAME, "graphql.txt", as_json=False)[len("for (;;);"):]
        self.assertEqual(len(self.m.list_jobs(T, self.h)), 7)


class Detail(MetaCase):
    def test_detail_reads_the_jobposting_block(self):
        row = self.m.list_jobs(T, self.h)[0]
        self.m.detail(T, row, self.h)
        self.assertIn("Own the roadmap for the invented finance systems team.", row["description"])
        self.assertIn("Run quarterly planning for ERP and procurement tools", row["description"])
        self.assertIn("5+ years of program management experience", row["description"])
        self.assertNotIn("<li>", row["description"])
        self.assertEqual(row["posted"], "2026-10-01")
        self.assertEqual(row["location"], "Menlo Park, CA | Remote, US | Remote")
        self.assertEqual(row["extra"]["employmentType"], "FULL_TIME")
        self.assertEqual(row["extra"]["validThrough"], "2026-12-31")
        get = self.session().calls[-1]
        self.assertEqual((get[0], get[1]), ("GET", row["url"]))
        self.assertEqual(get[2]["Sec-Fetch-Mode"], "navigate")
        self.assertEqual(self.h.robots_checked[-1], row["url"])


if __name__ == "__main__":
    unittest.main()
