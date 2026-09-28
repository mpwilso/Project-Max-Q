#!/usr/bin/env python3
"""privacy_scan.py - block a commit or push that carries personal data.

Keeps personal data (names, contact details, private ids) out of a public repository: every commit
and every push is scanned first.

    python tools/privacy_scan.py              # scan the working tree (tracked + untracked, not ignored)
    python tools/privacy_scan.py --staged     # scan what is staged (the pre-commit hook)
    python tools/privacy_scan.py --push       # scan everything reachable from any local ref (the pre-push hook)

The patterns live in .privacy/config.json, which is gitignored: a committed list of what must stay
private would publish it. A missing or unreadable config fails CLOSED (exit 2), never open.

config.json:
    {"patterns": ["regex", ...],                     # case-insensitive
     "allow": {"path/glob": ["regex", ...]},         # a pattern tolerated in matching files only
     "import_json_patterns": [{"file": "...", "path": "holds[*].patterns"}],   # optional
     "import_names": [{"file": "...", "path": "*.people[*].name"}],            # optional
     "import_tokens": [{"file": "...", "path": "...", "regex": "...", "min_len": 6}]}   # optional

Exit 0 clean, 1 on a hit, 2 when the scan could not run.
"""
import argparse, fnmatch, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".privacy" / "config.json"


def git(*args, binary=False):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def walk(obj, path):
    """Values at a tiny path language: 'a.b', '[*]' for every list item, '*' for every dict value,
    '~' for every dict key."""
    if not path:
        yield obj
        return
    m = re.match(r"(\[\*\]|\*|[^.\[]+)\.?(.*)", path)
    head, rest = m.group(1), m.group(2)
    if head == "[*]" and isinstance(obj, list):
        for x in obj:
            yield from walk(x, rest)
    elif head == "*" and isinstance(obj, dict):
        for x in obj.values():
            yield from walk(x, rest)
    elif head == "~" and isinstance(obj, dict):
        for x in obj:
            yield from walk(x, rest)
    elif isinstance(obj, dict) and head in obj:
        yield from walk(obj[head], rest)


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    pats = list(cfg.get("patterns", []))
    for src in cfg.get("import_json_patterns", []):
        data = json.loads(Path(src["file"]).read_text(encoding="utf-8-sig"))
        for v in walk(data, src["path"]):
            pats.extend(v if isinstance(v, list) else [v])
    for src in cfg.get("import_names", []):
        data = json.loads(Path(src["file"]).read_text(encoding="utf-8-sig"))
        for v in walk(data, src["path"]):
            # full names only: a lone first name ("Charlotte") is also a city in half the fixtures
            if isinstance(v, str) and " " in v.strip():
                pats.append(r"\b" + re.escape(v.strip()) + r"\b")
    for src in cfg.get("import_tokens", []):
        # ids pulled out of private records, matched literally
        data = json.loads(Path(src["file"]).read_text(encoding="utf-8-sig"))
        rx = re.compile(src.get("regex", r"(.+)"))
        for v in walk(data, src["path"]):
            for tok in rx.findall(v) if isinstance(v, str) else []:
                if len(tok) >= src.get("min_len", 6):
                    pats.append(re.escape(tok))
    compiled = [(p, re.compile(p, re.I)) for p in pats]
    allow = {g: [re.compile(p, re.I) for p in ps] for g, ps in cfg.get("allow", {}).items()}
    return compiled, allow


def tolerated(path, text, allow):
    return any(fnmatch.fnmatch(path, g) and any(a.search(text) for a in ps) for g, ps in allow.items())


def blobs_worktree():
    for p in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines():
        f = ROOT / p
        if f.is_file():
            yield p, f.read_bytes()


def blobs_staged():
    for line in git("ls-files", "--stage").splitlines():
        meta, p = line.split("\t", 1)
        yield p, git("cat-file", "blob", meta.split()[1], binary=True)


def blobs_push():
    # Every local ref, not just HEAD: `git push --all`, `--tags` or another branch sends history
    # HEAD never reaches.
    seen = set()
    for line in git("rev-list", "--objects", "--all").splitlines():
        sha, _, p = line.partition(" ")
        if not p or sha in seen:
            continue
        if git("cat-file", "-t", sha).strip() == "blob":
            seen.add(sha)
            yield p, git("cat-file", "blob", sha, binary=True)
    # commit messages, author/committer identities and file paths travel with a push too
    yield "<commit messages>", git("log", "--all", "--format=%B", binary=True)
    yield "<commit identities>", git("log", "--all", "--format=%an <%ae> | %cn <%ce>", binary=True)
    yield "<paths>", git("ls-tree", "-r", "--name-only", "HEAD", binary=True)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--push", action="store_true")
    a = ap.parse_args()
    try:
        pats, allow = load_config()
    except Exception as ex:
        print(f"privacy_scan: cannot load {CONFIG.relative_to(ROOT)} ({ex}). Failing closed.")
        return 2
    if not pats:
        print("privacy_scan: the blocklist is empty. Failing closed.")
        return 2
    src = blobs_staged() if a.staged else blobs_push() if a.push else blobs_worktree()
    hits = 0
    for path, data in src:
        text = data.decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            for raw, rx in pats:
                m = rx.search(line)
                if m and not tolerated(path, m.group(0), allow):
                    hits += 1
                    print(f"  {path}:{n}: matched {raw!r}")
    if hits:
        print(f"privacy_scan: {hits} hit(s). Nothing personal leaves this machine; fix and retry.")
        return 1
    print(f"privacy_scan: clean ({len(pats)} pattern(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
