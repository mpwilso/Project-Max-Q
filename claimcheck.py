"""claimcheck.py - block a resume that says anything the candidate's verified claims don't support.

Every bullet on the resume must cite at least one claim, as [claim-id], from the claims file. The
bullet is then checked against the facts of the claims it cites:

    figures   every number in the bullet must be one those claims state
    systems   every named system or tool must be one those claims support
    never     nothing from the never-claim list (people management, certifications he lacks, ...)
    titles    every role heading must be a title the candidate actually held

A single failing line blocks the whole resume (exit code 1). The report names the line and the reason,
so the fix is to change the wording or add a verified claim, never to argue with the check. A missing
or unreadable input file exits 2 with a one-line error.

    python claimcheck.py examples/john_doe/resume.md
    python claimcheck.py examples/john_doe/resume_blocked.md      # shows what gets blocked
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLAIMS = ROOT / "examples" / "john_doe" / "claims.json"

CITE = re.compile(r"\[([a-z0-9-]+)\]")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
ROLE_HEADING = re.compile(r"^###\s+(?P<title>[^·|]+?)\s*[·|]")


def has_phrase(text, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def check_bullet(line, claims, cfg):
    """Return the reasons a bullet is blocked; an empty list means it passes."""
    reasons = []
    cited = CITE.findall(line)
    text = CITE.sub("", line).lower()
    if not cited:
        return ["cites no claim"]
    unknown = [c for c in cited if c not in claims]
    if unknown:
        reasons.append("cites unknown claim(s): " + ", ".join(unknown))
    facts = {f for c in cited if c in claims for f in claims[c]["facts"]}
    for n in NUMBER.findall(text):
        if n not in facts:
            reasons.append(f"figure {n} is not in the cited claim(s)")
    for s in cfg.get("known_systems", []):
        if has_phrase(text, s) and s not in facts:
            reasons.append(f"names {s}, which the cited claim(s) don't support")
    for p in cfg.get("never_claim", []):
        if has_phrase(text, p):
            reasons.append(f"never-claim: {p}")
    return reasons


def check(resume_text, cfg):
    """Check every bullet and role heading. Returns a list of (line_no, line, reasons)."""
    claims = {c["id"]: c for c in cfg["claims"]}
    held = {t.lower() for t in cfg.get("titles_held", [])}
    results = []
    for n, raw in enumerate(resume_text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            results.append((n, line, check_bullet(line[2:], claims, cfg)))
        elif (m := ROLE_HEADING.match(line)):
            title = m.group("title").strip()
            results.append((n, line, [] if title.lower() in held else [f"title not held: {title}"]))
    return results


def main():
    ap = argparse.ArgumentParser(description="Block resume lines the verified claims don't support.")
    ap.add_argument("resume")
    ap.add_argument("--claims", default=str(CLAIMS))
    a = ap.parse_args()
    try:
        cfg = json.loads(Path(a.claims).read_text(encoding="utf-8-sig"))
        resume = Path(a.resume).read_text(encoding="utf-8-sig")
    except FileNotFoundError as e:
        print(f"claimcheck: file not found: {e.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"claimcheck: {a.claims}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as e:
        print(f"claimcheck: cannot read input ({e})", file=sys.stderr)
        return 2
    results = check(resume, cfg)
    blocked = [r for r in results if r[2]]
    for n, line, reasons in results:
        mark = "BLOCK" if reasons else "ok   "
        print(f"{mark} {n:3}  {line[:90]}")
        for r in reasons:
            print(f"            - {r}")
    print(f"\n{len(results)} line(s) checked, {len(blocked)} blocked: "
          f"{'BLOCKED, fix the lines above' if blocked else 'every line traces to a verified claim'}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
