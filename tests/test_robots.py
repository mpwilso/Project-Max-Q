"""robots.txt is checked before every fetch through the shared HTTP layer, on by default.

Each host's robots.txt is read once per run with the honest user agent; a path it disallows is not
fetched, and the employer is reported NOT READ with the path, in coverage and in the health block,
never as a quiet empty board. A robots.txt that cannot be fetched (404, a dropped connection) allows,
as the standard says, and the host is noted. --ignore-robots / MAXQ_IGNORE_ROBOTS=1 turns it off.

Offline: the per-thread requests.Session is replaced by a route table, so the robots.txt file and
every board answer come from this file. An unmatched URL raises, so no test can reach the network.
"""
import contextlib, datetime as dt, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from tests.stubs import sweep, gates, ROOT

G = gates()
ROBOTS = ("User-agent: *\nDisallow: /private/\n\n"
          "User-agent: maxq-sweep\nDisallow: /private/\nDisallow: /api/internal/\n")


class FakeResponse:
    headers = {}

    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self.payload = status, text, payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sweep.requests.exceptions.HTTPError(f"{self.status_code} Client Error", response=self)


class FakeSession:
    """Stands in for the per-thread requests.Session: answers by URL substring, records every call.
    A route's answer may be a FakeResponse, a callable(url) returning one, or an exception to raise."""

    def __init__(self, routes):
        self.routes, self.calls = list(routes), []

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url, dict(headers or {})))
        for sub, resp in self.routes:
            if sub in url:
                r = resp(url) if callable(resp) else resp
                if isinstance(r, BaseException): raise r
                return r
        raise AssertionError(f"FakeSession: no route for {url}")

    def urls(self):
        return [u for _m, u, _h in self.calls]

    def robots_fetches(self):
        return [u for u in self.urls() if u.endswith("/robots.txt")]


def robots(text):
    return ("/robots.txt", FakeResponse(200, text))


def board(payload=None):
    return ("", FakeResponse(200, "{}", payload if payload is not None else {"jobs": [1]}))


class RobotsCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MAXQ_IGNORE_ROBOTS", None)
        saved = (sweep.ROBOTS_CHECK, sweep.DELAY, sweep.http_session, sweep.time.sleep)
        self.addCleanup(self.restore, saved)
        self.addCleanup(sweep.robots_reset)
        sweep.robots_reset()
        sweep.ROBOTS_CHECK, sweep.DELAY = True, 0
        sweep.time.sleep = lambda s: None

    @staticmethod
    def restore(saved):
        sweep.ROBOTS_CHECK, sweep.DELAY, sweep.http_session, sweep.time.sleep = saved

    def serve(self, *routes):
        self.sess = FakeSession(routes)
        sweep.http_session = lambda: self.sess
        return self.sess


class AllowedPath(RobotsCase):
    def test_an_allowed_path_is_fetched_after_one_robots_read(self):
        s = self.serve(robots(ROBOTS), board({"jobs": [1, 2]}))
        self.assertEqual(sweep.get("https://a.test/jobs?page=1"), {"jobs": [1, 2]})
        self.assertEqual(s.urls(), ["https://a.test/robots.txt", "https://a.test/jobs?page=1"])

    def test_the_robots_read_carries_the_honest_user_agent(self):
        s = self.serve(robots(ROBOTS), board())
        sweep.get_text("https://a.test/jobs/")
        ua = s.calls[0][2].get("User-Agent")
        self.assertEqual(ua, sweep.UA)
        self.assertNotIn("Mozilla", ua)

    def test_a_group_for_this_tool_beats_the_wildcard(self):
        # robots.txt matches the user agent's product token, "maxq-sweep", against its groups.
        self.serve(robots("User-agent: *\nDisallow: /\n\nUser-agent: maxq-sweep\nAllow: /\n"), board())
        self.assertEqual(sweep.get("https://a.test/jobs"), {"jobs": [1]})
        sweep.robots_reset()
        self.serve(robots("User-agent: *\nAllow: /\n\nUser-agent: maxq-sweep\nDisallow: /api/\n"), board())
        with self.assertRaises(sweep.RobotsDisallowed):
            sweep.get("https://a.test/api/jobs")


class DisallowedPath(RobotsCase):
    def test_a_disallowed_path_is_not_fetched(self):
        s = self.serve(robots(ROBOTS), board())
        with self.assertRaises(sweep.RobotsDisallowed) as cm:
            sweep.get("https://a.test/private/jobs?x=1")
        self.assertEqual(s.urls(), ["https://a.test/robots.txt"])          # the board URL never went out
        self.assertEqual(cm.exception.path, "/private/jobs")
        self.assertIn("/private/jobs", str(cm.exception))
        self.assertEqual(sweep.not_read_status(cm.exception), "NOT READ: robots.txt disallows /private/jobs")

    def test_every_entry_point_of_the_http_layer_checks(self):
        s = self.serve(robots(ROBOTS), board())
        for call in (lambda: sweep.get("https://a.test/private/a"),
                     lambda: sweep.get_text("https://a.test/private/b"),
                     lambda: sweep.post_json("https://a.test/private/c", {"q": 1}),
                     lambda: sweep.get_conditional("https://a.test/private/d", None),
                     lambda: sweep.plugin_helpers().get("https://a.test/api/internal/e")):
            with self.assertRaises(sweep.RobotsDisallowed):
                call()
        self.assertEqual(s.urls(), ["https://a.test/robots.txt"])

    def test_not_read_is_neither_an_error_nor_a_skip(self):
        # The read loop keys ERROR for the retry pass and SKIPPED for the opt-in; this is a third state.
        e = sweep.RobotsDisallowed("https://a.test/private/jobs")
        self.assertFalse(isinstance(e, (ConnectionError, OSError)))
        st = sweep.not_read_status(e)
        self.assertTrue(st.startswith("NOT READ: "))
        self.assertFalse(st.startswith(("ERROR", "SKIPPED", "OK")))


class MissingRobots(RobotsCase):
    def test_a_404_allows_and_is_noted(self):
        s = self.serve(("/robots.txt", FakeResponse(404, "not found")), board())
        self.assertEqual(sweep.get("https://a.test/private/jobs"), {"jobs": [1]})
        self.assertEqual(s.urls(), ["https://a.test/robots.txt", "https://a.test/private/jobs"])
        self.assertEqual(sweep._ROBOTS_NOTES, {"https://a.test": "HTTP 404"})
        line = sweep.robots_summary()
        self.assertIn("1 host(s) checked", line)
        self.assertIn("a.test (HTTP 404)", line)
        self.assertIn("allowing", line)

    def test_a_dropped_connection_allows_and_is_noted(self):
        s = self.serve(("/robots.txt", sweep.requests.exceptions.ConnectionError("reset")), board())
        self.assertEqual(sweep.get("https://a.test/private/jobs"), {"jobs": [1]})
        self.assertEqual(sweep._ROBOTS_NOTES, {"https://a.test": "ConnectionError"})
        self.assertEqual(len(s.robots_fetches()), 2)        # the HTTP layer's one retry, then it gave up
        self.assertEqual(s.urls()[-1], "https://a.test/private/jobs")

    def test_a_robots_file_with_no_rules_allows(self):
        self.serve(robots(""), board())
        self.assertEqual(sweep.get("https://a.test/private/jobs"), {"jobs": [1]})
        self.assertEqual(sweep._ROBOTS_NOTES, {})
        self.assertEqual(sweep.robots_summary(), "robots.txt: 1 host(s) checked")


class CachePerHost(RobotsCase):
    def test_one_robots_fetch_per_host_per_run(self):
        s = self.serve(robots(ROBOTS), board())
        for u in ("https://a.test/jobs", "https://a.test/jobs?page=2", "https://a.test/job/1",
                  "https://b.test/jobs", "https://b.test/jobs?page=2"):
            sweep.get(u)
        with self.assertRaises(sweep.RobotsDisallowed):
            sweep.get("https://a.test/private/x")
        self.assertEqual(s.robots_fetches(), ["https://a.test/robots.txt", "https://b.test/robots.txt"])
        self.assertEqual(len(s.calls), 2 + 5)
        self.assertIn("2 host(s) checked", sweep.robots_summary())

    def test_a_missing_file_is_cached_too(self):
        s = self.serve(("/robots.txt", FakeResponse(404, "")), board())
        sweep.get("https://a.test/jobs"); sweep.get("https://a.test/jobs?page=2")
        self.assertEqual(len(s.robots_fetches()), 1)

    def test_a_new_run_reads_robots_again(self):
        s = self.serve(robots(ROBOTS), board())
        sweep.get("https://a.test/jobs")
        sweep.robots_reset()
        sweep.get("https://a.test/jobs")
        self.assertEqual(len(s.robots_fetches()), 2)


class OffSwitch(RobotsCase):
    def test_on_by_default(self):
        self.assertTrue(sweep.robots_check_enabled())

    def test_the_env_var_turns_the_check_off(self):
        os.environ["MAXQ_IGNORE_ROBOTS"] = "1"
        s = self.serve(robots(ROBOTS), board())
        self.assertEqual(sweep.get("https://a.test/private/jobs"), {"jobs": [1]})
        self.assertEqual(s.robots_fetches(), [])
        self.assertIn("check OFF", sweep.robots_summary())
        os.environ["MAXQ_IGNORE_ROBOTS"] = "0"
        self.assertTrue(sweep.robots_check_enabled())

    def test_the_flag_turns_the_check_off(self):
        sweep.ROBOTS_CHECK = False
        s = self.serve(robots(ROBOTS), board())
        self.assertEqual(sweep.get("https://a.test/private/jobs"), {"jobs": [1]})
        self.assertEqual(s.robots_fetches(), [])


class HealthBlock(unittest.TestCase):
    def health(self, coverage):
        r = sweep.norm("Contoso", "greenhouse", 1, "Technical Program Manager", "Remote, United States", "https://x/1")
        return sweep.sweep_health({r["key"]: r}, {r["key"]: ("PASS", [])}, [], coverage, {}, {}, {}, len(coverage))

    def test_a_disallowed_employer_is_listed_with_the_path_and_is_not_uncovered(self):
        cov = [("Contoso", "greenhouse", "NOT READ: robots.txt disallows /private/jobs", 0),
               ("Fabrikam", "lever", "OK", 12)]
        h = self.health(cov)
        self.assertEqual(h["errors"], [])
        self.assertEqual(h["robots"], [("Contoso", "NOT READ: robots.txt disallows /private/jobs")])
        w = h["warnings"]
        self.assertTrue(any(x.startswith("1 employer(s) not read, robots.txt disallows the path: "
                                         "Contoso (robots.txt disallows /private/jobs)") for x in w), w)
        self.assertFalse(any("UNCOVERED" in x for x in w), w)
        self.assertTrue(any("Contoso (robots.txt disallows /private/jobs)" in x for x in sweep.health_lines(h)))

    def test_rows_of_a_disallowed_board_are_carried_not_closed(self):
        prev = {"greenhouse:Contoso:7": {"company": "Contoso", "ats": "greenhouse", "id": "7"}}
        rows = {}
        cov = [("Contoso", "greenhouse", "NOT READ: robots.txt disallows /private/jobs", 0)]
        carried = sweep.carry_forward(rows, prev, cov, [{"company": "Contoso", "ats": "greenhouse"}])
        self.assertEqual(carried, {"greenhouse:Contoso:7": sweep.CARRY_ERROR})


class RunEndToEnd(RobotsCase):
    """run() with a board reader whose list URL robots.txt disallows: the coverage row says NOT READ with
    the path, the health block carries the reason, the URL never goes out, and the retry pass does not
    read the board again."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        keys = ("DATA", "JDS", "SCORED", "CONTACTS", "TODAY", "REPORTS", "READLOG", "gh_detail_fetch")
        self.saved_paths = {k: getattr(sweep, k) for k in keys}
        sweep.gh_detail_fetch = lambda slug, jid: {}         # the form pass is not under test here
        sweep.DATA = base / "data"; sweep.JDS = sweep.DATA / "jds"; sweep.JDS.mkdir(parents=True)
        sweep.SCORED = sweep.DATA / "scored.json"; sweep.CONTACTS = sweep.DATA / "contacts.json"
        sweep.REPORTS = base / "reports"; sweep.REPORTS.mkdir()
        sweep.READLOG = sweep.DATA / "read_log.json"
        sweep.TODAY = dt.date(2026, 10, 20)
        self.saved_adapter = sweep.ADAPTERS.get("greenhouse")
        os.environ["MAXQ_SERIAL"] = "1"

        def lister(t, smoke=False):
            d = sweep.get(f"https://boards.test/{t['slug']}/private/jobs")
            return [sweep.norm(t["company"], "greenhouse", j["id"], j["title"], "Remote, United States",
                               f"https://boards.test/{t['slug']}/jobs/{j['id']}") for j in d["jobs"]]
        sweep.ADAPTERS["greenhouse"] = lister
        self.targets = [{"company": "Contoso", "ats": "greenhouse", "slug": "contoso", "verified": True},
                        {"company": "Fabrikam", "ats": "greenhouse", "slug": "fabrikam", "verified": True}]

    def tearDown(self):
        for k, v in self.saved_paths.items(): setattr(sweep, k, v)
        sweep.ADAPTERS["greenhouse"] = self.saved_adapter
        os.environ.pop("MAXQ_SERIAL", None)
        self.tmp.cleanup()

    def test_disallowed_board_reaches_coverage_and_the_health_block(self):
        s = self.serve(robots("User-agent: *\nDisallow: /contoso/private/\n"),
                       board({"jobs": [{"id": 1, "title": "Technical Program Manager, Finance Systems"}]}))
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.run(self.targets, G, fresh=True)
        snap = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        status = {c: st for c, a, st, n in snap["coverage"]}
        self.assertEqual(status["Contoso"], "NOT READ: robots.txt disallows /contoso/private/jobs")
        self.assertEqual(status["Fabrikam"], "OK")
        self.assertNotIn("https://boards.test/contoso/private/jobs", s.urls())
        self.assertEqual(s.urls().count("https://boards.test/fabrikam/private/jobs"), 1)
        self.assertEqual(s.robots_fetches(), ["https://boards.test/robots.txt"])    # one host, one read
        w = snap["health"]["warnings"]
        self.assertTrue(any("Contoso (robots.txt disallows /contoso/private/jobs)" in x for x in w), w)
        self.assertFalse(any("UNCOVERED" in x for x in w), w)
        self.assertEqual(snap["health"]["errors"], [])
        self.assertIn("robots.txt: 1 host(s) checked", snap["health"]["notes"])


class CommandLine(unittest.TestCase):
    def test_sweep_and_probe_offer_the_off_switch(self):
        for script in ("sweep.py", os.path.join("adapters", "probe.py")):
            out = subprocess.run([sys.executable, script, "--help"], cwd=ROOT, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("--ignore-robots", out.stdout, script)
            self.assertIn("MAXQ_IGNORE_ROBOTS", out.stdout, script)


if __name__ == "__main__":
    unittest.main()
