"""Honest by default: the sweep names itself in every request, and browser impersonation is opt-in.

Two paths pretend to be a browser: the Meta reader (a Chrome user agent, browser navigation headers,
a token lifted from the page) and Workday POSTs through curl_cffi with impersonate="chrome"
(USE_CURL_CFFI=1). Both stay off unless the run opts in with --allow-browser-impersonation or
MAXQ_ALLOW_BROWSER_IMPERSONATION=1. Off, the Meta reader skips with a plain reason and Workday uses
plain requests.

Offline: every HTTP call is answered by a fake."""
import os, subprocess, sys, types, unittest
from unittest import mock

from tests.stubs import sweep, ROOT

ENV_KEYS = ("MAXQ_ALLOW_BROWSER_IMPERSONATION", "USE_CURL_CFFI")


class FakeResponse:
    status_code = 200
    headers = {}
    text = "<html></html>"

    def json(self):
        return {"ok": True}

    def raise_for_status(self):
        return None


class Recorder:
    """Stands in for the per-thread requests.Session: records every call, answers 200."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url, dict(headers or {})))
        return FakeResponse()


class OptInCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        saved = (sweep.ALLOW_BROWSER_IMPERSONATION, sweep.DELAY, sweep._meta_sess, sweep.http_session)
        self.addCleanup(self.restore, saved)
        sweep.ALLOW_BROWSER_IMPERSONATION = False
        sweep.DELAY = 0
        sweep._meta_sess = None
        self.rec = Recorder()
        sweep.http_session = lambda: self.rec
        self.curl_calls = []
        fake_cr = types.SimpleNamespace(post=lambda url, **kw: self.curl_calls.append((url, kw)) or FakeResponse())
        mods = mock.patch.dict(sys.modules, {"curl_cffi": types.SimpleNamespace(requests=fake_cr)})
        mods.start()
        self.addCleanup(mods.stop)

    @staticmethod
    def restore(saved):
        (sweep.ALLOW_BROWSER_IMPERSONATION, sweep.DELAY, sweep._meta_sess, sweep.http_session) = saved

    def assert_honest(self, headers):
        ua = headers.get("User-Agent", "")
        self.assertEqual(ua, sweep.UA)
        self.assertNotIn("Mozilla", ua)
        self.assertNotIn("Chrome", ua)


class HonestUserAgent(OptInCase):
    def test_the_user_agent_names_the_tool_and_links_the_repo(self):
        self.assertRegex(sweep.UA, r"^maxq-sweep/\d+\.\d+ \(\+https://github\.com/mpwilso/Project-Max-Q\)$")

    def test_default_calls_send_the_honest_user_agent(self):
        sweep.get("https://example.test/a")
        sweep.post_json("https://example.test/b", {"x": 1})
        sweep.get_text("https://example.test/c")
        self.assertEqual(len(self.rec.calls), 3)
        for _m, _u, h in self.rec.calls:
            self.assert_honest(h)

    def test_off_by_default(self):
        self.assertFalse(sweep.browser_impersonation_allowed())


class CurlCffiNeedsTheOptIn(OptInCase):
    def test_use_curl_cffi_alone_does_not_impersonate(self):
        os.environ["USE_CURL_CFFI"] = "1"
        sweep.post_json("https://tenant.wd1.myworkdayjobs.com/wday/cxs/t/s/jobs", {"limit": 20})
        self.assertEqual(self.curl_calls, [])
        self.assertEqual(len(self.rec.calls), 1)
        self.assert_honest(self.rec.calls[0][2])

    def test_the_opt_in_routes_workday_posts_through_curl_cffi(self):
        os.environ["USE_CURL_CFFI"] = "1"
        sweep.ALLOW_BROWSER_IMPERSONATION = True
        sweep.post_json("https://tenant.wd1.myworkdayjobs.com/wday/cxs/t/s/jobs", {"limit": 20})
        self.assertEqual(len(self.curl_calls), 1)
        self.assertEqual(self.curl_calls[0][1].get("impersonate"), "chrome")
        self.assertEqual(self.rec.calls, [])

    def test_the_opt_in_alone_does_not_need_curl_cffi(self):
        sweep.ALLOW_BROWSER_IMPERSONATION = True
        sweep.post_json("https://tenant.wd1.myworkdayjobs.com/wday/cxs/t/s/jobs", {"limit": 20})
        self.assertEqual(self.curl_calls, [])
        self.assertEqual(len(self.rec.calls), 1)

    def test_the_env_var_is_the_same_opt_in(self):
        os.environ["MAXQ_ALLOW_BROWSER_IMPERSONATION"] = "1"
        self.assertTrue(sweep.browser_impersonation_allowed())
        os.environ["MAXQ_ALLOW_BROWSER_IMPERSONATION"] = "0"
        self.assertFalse(sweep.browser_impersonation_allowed())


LSD_PAGE = '<html>"LSD",[],{"token":"tok123"} "server_revision":42</html>'


class FakeSession:
    instances = []

    def __init__(self):
        self.headers = {}
        self.calls = []
        FakeSession.instances.append(self)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, dict(self.headers, **(headers or {}))))
        return types.SimpleNamespace(text=LSD_PAGE, raise_for_status=lambda: None)


class MetaNeedsTheOptIn(OptInCase):
    def setUp(self):
        super().setUp()
        FakeSession.instances = []
        p = mock.patch.object(sweep.requests, "Session", FakeSession)
        p.start()
        self.addCleanup(p.stop)

    def test_default_skips_meta_with_a_plain_reason_and_sends_nothing(self):
        with self.assertRaises(sweep.OptInRequired) as cm:
            sweep.metacareers({"company": "Meta", "ats": "metacareers"})
        msg = str(cm.exception)
        self.assertIn("Meta's careers site needs browser impersonation", msg)
        self.assertIn("--allow-browser-impersonation", msg)
        self.assertEqual(FakeSession.instances, [])          # no session, no request, no browser UA
        self.assertEqual(self.rec.calls, [])

    def test_the_opt_in_lets_the_meta_reader_run(self):
        sweep.ALLOW_BROWSER_IMPERSONATION = True
        s = sweep._meta_session()
        self.assertEqual(s._lsd, "tok123")
        self.assertIn("Chrome", FakeSession.instances[0].calls[0][2]["User-Agent"])

    def test_the_skip_is_not_an_error(self):
        # The read loop and the retry pass key on "ERROR"; a skip must not look like one.
        self.assertFalse(issubclass(sweep.OptInRequired, (ConnectionError, OSError)))
        self.assertTrue(sweep.skip_status(sweep.OptInRequired("x")).startswith("SKIPPED: "))


class CommandLine(unittest.TestCase):
    def test_sweep_and_probe_offer_the_flag(self):
        for script in ("sweep.py", os.path.join("adapters", "probe.py")):
            out = subprocess.run([sys.executable, script, "--help"], cwd=ROOT, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("--allow-browser-impersonation", out.stdout, script)


if __name__ == "__main__":
    unittest.main()
