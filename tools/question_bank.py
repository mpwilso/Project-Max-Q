"""question_bank.py - the interview questions and signals from every round, rolled up.

After each round, the debrief appends one JSON line per question asked and per signal (what the
interviewer said the team wants or worries about) to a questions file (docs/INTERVIEW_PREP.md has the
schema). This reads it back three ways:

    python tools/question_bank.py                        # questions asked anywhere, most-asked first
    python tools/question_bank.py --company ExampleCo    # one employer: its signals, then its questions
    python tools/question_bank.py --check prep/X.prep.md # questions asked before that the pack has no
                                                         # "## " answer heading for

A question asked once tends to come back in the next round and at the next company, so a prep pack is
checked against the bank before it is used. Matching is lexical (shared content words between a question
and a "## " heading), so a "not covered" line is a prompt to look, not a verdict. Read-only; stdlib only.

The default file is data/interviews/questions.jsonl, which is gitignored: real interview notes stay on
your machine. The fictional example is examples/john_doe/interviews/questions.jsonl (pass --bank).
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "data" / "interviews" / "questions.jsonl"
EXAMPLE = ROOT / "examples" / "john_doe" / "interviews" / "questions.jsonl"
STOP = set("""a an the and or but of to in on for with at by from as is are was were be been being do does did
you your yours we our i me my it its this that these those what which who whom how why when where would could
should can will tell about give example time walk through describe talk any some there their them they have has
had if so than then just really ever more most one""".split())


def load(path=BANK):
    """Every parseable line of the file; a malformed line is skipped, a missing file is empty."""
    rows = []
    if not Path(path).exists():
        return rows
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _stem(w):
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)]
    return w


def words(text):
    """Content words, lightly stemmed. Trailing punctuation never sticks ("tool." is "tool")."""
    toks = (t.strip(".-") for t in re.findall(r"[a-z][a-z0-9+#.-]*", (text or "").lower()))
    return {_stem(t) for t in toks if t and t not in STOP and len(t) > 2}


def similar(a, b, threshold=0.5):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def group_questions(rows):
    """[(representative question, [rows])], most-asked first. Near-duplicate wordings fold together."""
    groups = []
    for r in rows:
        if r.get("type") != "question" or not r.get("question"):
            continue
        for g in groups:
            if similar(g[0], r["question"]):
                g[1].append(r)
                break
        else:
            groups.append((r["question"], [r]))
    return sorted(groups, key=lambda g: (-len(g[1]), g[0]))


def headings(prep_text):
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)$", prep_text, re.M)]


def uncovered(prep_text, rows, threshold=0.4):
    """Questions asked before that no '## ' heading in the pack answers."""
    heads = headings(prep_text)
    out = []
    for rep, rs in group_questions(rows):
        if not any(similar(rep, h, threshold) or any(similar(r["question"], h, threshold) for r in rs) for h in heads):
            out.append((rep, rs))
    return out


def fmt_group(rep, rs):
    cos = sorted({r.get("company", "?") for r in rs})
    landed = [r.get("landed") for r in rs if r.get("landed")]
    used = sorted({r.get("story_used") for r in rs if r.get("story_used")})
    line = f"{len(rs)}x  {rep}\n     asked at: {', '.join(cos)}"
    if used:
        line += f" | stories: {', '.join(used)}"
    if landed:
        line += f" | landed: {', '.join(landed)}"
    return line


def main(argv=None):
    ap = argparse.ArgumentParser(description="Roll up the interview question bank")
    ap.add_argument("--company")
    ap.add_argument("--check", metavar="PREP_MD")
    ap.add_argument("--bank", default=str(BANK))
    a = ap.parse_args(argv)
    rows = load(a.bank)
    if not rows:
        hint = "" if Path(a.bank) == EXAMPLE else f" (the fictional example: --bank {EXAMPLE.relative_to(ROOT).as_posix()})"
        print(f"no entries in {a.bank}{hint}")
        return 0
    if a.check:
        try:
            text = Path(a.check).read_text(encoding="utf-8-sig")
        except OSError as e:
            print(f"question_bank: cannot read {a.check} ({e})", file=sys.stderr)
            return 2
        miss = uncovered(text, rows)
        print(f"{len(miss)} question(s) asked before with no answer heading in {a.check}:")
        for rep, rs in miss:
            print("  " + fmt_group(rep, rs).replace("\n", "\n  "))
        return 0
    if a.company:
        key = a.company.lower()
        rows = [r for r in rows if key in (r.get("company") or "").lower()]
        sig = [r for r in rows if r.get("type") == "signal"]
        if sig:
            print(f"Signals ({len(sig)}):")
            for r in sig:
                print(f"  {r.get('date', '')} {r.get('stage', '')}: {r.get('signal', '')}"
                      + (f"\n     use next: {r['use_next']}" if r.get("use_next") else ""))
    groups = group_questions(rows)
    print(f"Questions ({sum(len(g[1]) for g in groups)} asked, {len(groups)} distinct):")
    for rep, rs in groups:
        print("  " + fmt_group(rep, rs).replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
