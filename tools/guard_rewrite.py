"""pre-commit guard: a staged file that was rewritten, not edited, is not committed.

A coding agent that edits files with shell tools can rewrite a whole file while meaning to change one
line: `sed -i` under Git Bash turns CRLF into LF, and a script that loads and dumps JSON with a
different indent changes every line. The diff is then the whole file, and the real change is buried.
A note in the agent's instructions did not stop it; this check does. Each staged file with status M
is compared HEAD blob against index blob, as bytes:

  line-ending flip   HEAD was 90%+ CRLF (5+ newlines) and the index is 90%+ LF, or the reverse
  JSON reformat      a .json with 20+ lines at HEAD where over 60% of HEAD's lines are gone

Binary blobs, blobs over 5 MB, and data/snapshots/ are skipped. Added and deleted files are ignored.
Exit 1 on any finding. MAXQ_ALLOW_REWRITE=1 turns the findings into warnings and exits 0; setting it
is the maintainer's call, never the agent's.

    python tools/guard_rewrite.py [--repo PATH]
"""
import argparse, os, subprocess, sys
from collections import Counter

SKIP_PREFIXES = ("data/snapshots/",)
MAX_BYTES = 5 * 1024 * 1024
EOL_SHARE = 0.9
EOL_MIN_NEWLINES = 5
JSON_MIN_LINES = 20
JSON_GONE_SHARE = 0.6
TO_CRLF = ("python -c \"import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
           "b=p.read_bytes().replace(b'\\r\\n',b'\\n'); p.write_bytes(b.replace(b'\\n',b'\\r\\n'))\" {path}")
TO_LF = ("python -c \"import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
         "p.write_bytes(p.read_bytes().replace(b'\\r\\n',b'\\n'))\" {path}")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def modified(repo):
    """(path, head_sha, index_sha) for each staged M entry with a regular-file mode on both sides."""
    raw = git(repo, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--diff-filter=M").split(b"\0")
    out, i = [], 0
    while i + 1 < len(raw) and raw[i].startswith(b":"):
        old_mode, new_mode, old_sha, new_sha = raw[i][1:].split()[:4]
        path = raw[i + 1].decode("utf-8", "surrogateescape")
        i += 2
        if old_mode.startswith(b"100") and new_mode.startswith(b"100") and old_sha != new_sha:
            out.append((path, old_sha.decode(), new_sha.decode()))
    return out


def eol_style(blob, min_newlines):
    n = blob.count(b"\n")
    if n < max(min_newlines, 1):
        return None
    crlf = blob.count(b"\r\n")
    if crlf / n >= EOL_SHARE:
        return "CRLF"
    if (n - crlf) / n >= EOL_SHARE:
        return "LF"
    return None


def lines(blob):
    body = blob[:-1] if blob.endswith(b"\n") else blob
    return [l[:-1] if l.endswith(b"\r") else l for l in body.split(b"\n")] if body else []


def check(path, head, index):
    found = []
    was, now = eol_style(head, EOL_MIN_NEWLINES), eol_style(index, 1)
    if was and now and was != now:
        fix = (TO_CRLF if was == "CRLF" else TO_LF).format(path=path)
        found.append(f"{path}: line endings flipped {was} -> {now} (a whole-file diff). Restore {was} with: "
                     f"{fix} ; then git add {path}. Do not run `sed -i` on repo files; edit with a tool or a "
                     f"script that writes the bytes back unchanged.")
    head_lines, index_lines = lines(head), lines(index)
    if path.lower().endswith(".json") and len(head_lines) >= JSON_MIN_LINES:
        gone = sum((Counter(head_lines) - Counter(index_lines)).values())
        if gone / len(head_lines) > JSON_GONE_SHARE:
            found.append(f"{path}: reformatted, not edited ({gone} of {len(head_lines)} HEAD lines gone); write it "
                         f"back with the original indent/separators (check indent=, separators= and "
                         f"ensure_ascii= against HEAD) so only the changed lines differ.")
    return found


def scan(repo):
    findings = []
    for path, old, new in modified(repo):
        if path.startswith(SKIP_PREFIXES):
            continue
        if max(int(git(repo, "cat-file", "-s", old)), int(git(repo, "cat-file", "-s", new))) > MAX_BYTES:
            continue
        head, index = git(repo, "cat-file", "blob", old), git(repo, "cat-file", "blob", new)
        if b"\0" in head or b"\0" in index:
            continue
        findings += check(path, head, index)
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="repository to check (default: the current directory)")
    repo = ap.parse_args().repo
    allow = os.environ.get("MAXQ_ALLOW_REWRITE") == "1"
    try:
        findings = scan(repo)
    except Exception as e:      # a guard that cannot read the index must not wave the commit through
        findings = [f"guard_rewrite.py could not check the staged files: {e}"]
    if not findings:
        return 0
    tag = "WARNING (MAXQ_ALLOW_REWRITE=1)" if allow else "BLOCKED"
    for f in findings:
        print(f"pre-commit: {tag}: {f}", file=sys.stderr)
    if allow:
        print("pre-commit: MAXQ_ALLOW_REWRITE=1 is set, so the commit goes ahead with the findings above.",
              file=sys.stderr)
        return 0
    print("pre-commit: nothing was committed. If the rewrite is intended, commit once with "
          "MAXQ_ALLOW_REWRITE=1 set (the maintainer's call, never the agent's).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
