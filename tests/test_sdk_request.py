"""The Claude scorer against the real anthropic SDK, offline. The SDK client is built with a dummy key and
an HTTP client whose transport answers from this file, so nothing reaches the network. It checks what the
installed SDK actually sends: a version bump that drops or renames a parameter fails here, not on a paid
run. CI runs it in a job that installs requirements-ai.txt and sets MAXQ_REQUIRE_SDK=1, so there it
fails rather than skips; anywhere else, without the SDK, it skips."""
import json, os, unittest

from tests.stubs import ROOT
import fitscore

try:
    import anthropic
except ImportError:
    anthropic = None
REQUIRED = os.environ.get("MAXQ_REQUIRE_SDK") == "1"

GOOD = {"areas": {"role_match": 27, "skills": 22, "seniority": 16, "domain": 14, "logistics": 9},
        "score": 88, "strengths": ["NetSuite ownership"], "gaps": [], "dealbreakers": [], "summary": "Strong fit."}


def http_module():
    """The HTTP library the installed SDK is built on: httpx2 in the 2.x line, httpx before it."""
    from anthropic import _base_client
    http = getattr(_base_client, "httpx2", None) or getattr(_base_client, "httpx", None)
    if http is None:
        raise AssertionError("the installed anthropic SDK uses neither httpx2 nor httpx: update this test")
    return http


@unittest.skipIf(anthropic is None and not REQUIRED, "anthropic is not installed (pip install -r requirements-ai.txt)")
class RealSdkRequest(unittest.TestCase):
    def setUp(self):
        if anthropic is None:
            self.fail("MAXQ_REQUIRE_SDK=1 and anthropic is not installed")
        self.seen = []
        http = http_module()

        def answer(request):
            self.seen.append(request)
            body = {"id": "msg_test", "type": "message", "role": "assistant", "model": fitscore.MODEL,
                    "content": [{"type": "text", "text": json.dumps(GOOD)}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 1200, "output_tokens": 300,
                              "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}}
            return http.Response(200, json=body, headers={"request-id": "req_test"})

        self.http_client = http.Client(transport=http.MockTransport(answer))
        client = anthropic.Anthropic(api_key="sk-ant-dummy", base_url="https://api.anthropic.com",
                                     max_retries=0, http_client=self.http_client)
        self.scorer = fitscore.ClaudeScorer(client=client)
        line = (ROOT / "evals" / "fit" / "labeled.jsonl").read_text(encoding="utf-8").splitlines()[0]
        self.posting = json.loads(line)

    def tearDown(self):
        self.http_client.close()

    def test_one_eval_posting_is_sent_as_the_scorer_means_and_the_answer_is_parsed(self):
        res = self.scorer.score(self.posting)
        self.assertEqual(len(self.seen), 1)
        req = self.seen[0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url.path, "/v1/messages")
        self.assertIn("server-side-fallback-2026-07-01", req.headers.get("anthropic-beta", ""))
        # the 2.x SDK sends the key as a bearer token and dates the API it speaks
        self.assertEqual(req.headers.get("authorization"), "Bearer sk-ant-dummy")
        self.assertTrue(req.headers.get("anthropic-version"))
        body = json.loads(req.content)
        self.assertEqual(body["model"], fitscore.MODEL)
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"], {"type": "json_schema", "schema": fitscore.SCHEMA})
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("John Doe", body["system"][0]["text"])
        self.assertIn(self.posting["title"], body["messages"][0]["content"])
        self.assertNotIn("betas", body)                                   # a header, never a body key
        self.assertEqual((res["score"], res["verdict"]), (88, "apply"))
        self.assertEqual(self.scorer.usage, {"input_tokens": 1200, "output_tokens": 300,
                                             "cache_read_input_tokens": 1000})


if __name__ == "__main__":
    unittest.main()
