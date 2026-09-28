"""tools/guard_rewrite.py, the pre-commit step that refuses a staged file that was rewritten rather
than edited: a line-ending flip, or a JSON file re-serialized with a different indent. Each test builds
a throwaway repo, commits a fixture, stages a bad or good version, and runs the guard against it.
Exit 1 blocks; exit 0 lets the commit run."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "guard_rewrite.py"


class GuardRepo(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="guard_rewrite_"))
        self.git("init", "-q")
        for k, v in (("core.autocrlf", "false"), ("user.name", "t"), ("user.email", "t@example.com")):
            self.git("config", k, v)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True)

    def write(self, rel, data):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.git("add", rel)

    def commit(self, files):
        for rel, data in files.items():
            self.write(rel, data)
        self.git("commit", "-q", "--no-verify", "-m", "fixture")

    def guard(self, allow=False):
        env = {k: v for k, v in os.environ.items() if k != "MAXQ_ALLOW_REWRITE"}
        if allow:
            env["MAXQ_ALLOW_REWRITE"] = "1"
        r = subprocess.run([sys.executable, str(GUARD), "--repo", str(self.dir)],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr


def crlf_text(n=30):
    return "".join(f"line {i} of the notes file\r\n" for i in range(n)).encode()


def json_blob(obj, **kw):
    return json.dumps(obj, **kw).encode()


SAMPLE = {"employers": {f"Employer {i}": {"board": f"https://example.test/{i}", "note": f"row {i}"} for i in range(12)}}


class LineEndingFlips(GuardRepo):
    def test_crlf_to_lf_is_blocked(self):
        self.commit({"NOTES.md": crlf_text()})
        self.write("NOTES.md", crlf_text().replace(b"\r\n", b"\n"))
        code, err = self.guard()
        self.assertEqual(code, 1, err)
        self.assertIn("pre-commit:", err)
        self.assertIn("NOTES.md: line endings flipped CRLF -> LF", err)
        self.assertIn("sed -i", err)
        self.assertIn("MAXQ_ALLOW_REWRITE=1", err)

    def test_lf_to_crlf_is_blocked_too(self):
        lf = crlf_text().replace(b"\r\n", b"\n")
        self.commit({"notes.py": lf})
        self.write("notes.py", crlf_text())
        code, err = self.guard()
        self.assertEqual(code, 1, err)
        self.assertIn("notes.py: line endings flipped LF -> CRLF", err)

    def test_an_ordinary_crlf_edit_passes(self):
        self.commit({"NOTES.md": crlf_text()})
        self.write("NOTES.md", crlf_text().replace(b"line 7 of", b"line seven of") + b"a new line\r\n")
        code, err = self.guard()
        self.assertEqual((code, err), (0, ""))

    def test_a_flip_under_snapshots_is_skipped(self):
        self.commit({"data/snapshots/x.md": crlf_text()})
        self.write("data/snapshots/x.md", crlf_text().replace(b"\r\n", b"\n"))
        self.assertEqual(self.guard()[0], 0)

    def test_a_binary_file_is_skipped(self):
        blob = b"\x89PNG\x00\x01" + crlf_text()
        self.commit({"img.png": blob})
        self.write("img.png", blob.replace(b"\r\n", b"\n"))
        code, err = self.guard()
        self.assertEqual((code, err), (0, ""))


class JsonReformats(GuardRepo):
    def test_a_reindented_json_is_blocked(self):
        self.commit({"config/sample.json": json_blob(SAMPLE, indent=2)})
        self.write("config/sample.json", json_blob(SAMPLE, indent=1))
        code, err = self.guard()
        self.assertEqual(code, 1, err)
        self.assertIn("config/sample.json: reformatted, not edited", err)
        self.assertIn("original indent/separators", err)

    def test_a_json_collapsed_to_one_line_is_blocked(self):
        self.commit({"other.json": json_blob(SAMPLE, indent=2)})
        self.write("other.json", json_blob(SAMPLE))
        self.assertEqual(self.guard()[0], 1)

    def test_a_small_json_edit_passes(self):
        self.commit({"config/sample.json": json_blob(SAMPLE, indent=2)})
        edited = json.loads(json.dumps(SAMPLE))
        edited["employers"]["Employer 3"]["note"] = "edited"
        edited["employers"]["Employer 99"] = {"board": "https://example.test/99", "note": "added"}
        self.write("config/sample.json", json_blob(edited, indent=2))
        code, err = self.guard()
        self.assertEqual((code, err), (0, ""))


class OverrideAndScope(GuardRepo):
    def test_the_env_override_warns_and_exits_0(self):
        self.commit({"NOTES.md": crlf_text()})
        self.write("NOTES.md", crlf_text().replace(b"\r\n", b"\n"))
        code, err = self.guard(allow=True)
        self.assertEqual(code, 0, err)
        self.assertIn("pre-commit: WARNING", err)
        self.assertIn("line endings flipped", err)

    def test_added_and_deleted_files_are_ignored(self):
        self.commit({"NOTES.md": crlf_text(), "old.json": json_blob(SAMPLE, indent=2)})
        self.git("rm", "-q", "old.json")
        self.write("new.md", crlf_text().replace(b"\r\n", b"\n"))
        self.assertEqual(self.guard()[0], 0)


if __name__ == "__main__":
    unittest.main()
