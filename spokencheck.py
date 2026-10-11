"""spokencheck.py - the truth check for what gets said aloud: interview prep, story files, mock transcripts.

Run it through claimcheck, which loads and validates the claims file first:

    python claimcheck.py --spoken examples/john_doe/prep/ExampleCo_PM.prep.md
    python claimcheck.py --spoken examples/john_doe/stories/          # every .md but INDEX.md and README.md
    python claimcheck.py --spoken screen.prep.md --stage screen        # answers past 30 seconds warn

Nothing here is sent, so the page rules that exist for recruiters (hedges, dashes) do not apply, and an
estimate needs its "estimated". The truth rules do apply, in the voice the claim allows.

File convention (docs/INTERVIEW_PREP.md):
    ## <question>                     one heading per spoken answer; any other heading ("### Notes", "# Part 2")
                                      ends the answer above it, and nothing is read until the next "## "
    <!-- ... -->                      coaching notes, not read, except these:
      traces: id; id                  (in the file's first comment) the claims every answer may draw figures from
      resume: ../resume.md            (in the first comment) the page the interviewer holds
      <!-- cites: id; id -->          (inside one answer) more claims for that answer only
      <!-- untraced-ok: $95K to $110K -->   figures that are not claims (a posted band, a date), allowed only
                                      where that exact phrase is said
      <!-- done: debriefed <date> --> the round is over: failures still block, warnings stop printing

Checks (FAIL blocks, exit 1; WARN and INFO print):
    people_management  FAIL  managed / supervised / oversaw / hired + a team or reports, "reported to me", pods
    held_titles        FAIL  a title-cased title not held; naming the posting ("the Product Manager role") is fine
    never_claims       FAIL  the never-claim list; a negated or pursued one ("I'm not certified") is the truthful answer
    spoken_estimate    FAIL  a claim with `requires` said without it in the same sentence
    figure_trace       FAIL  a figure no claim states, or a citation to a claim that does not exist
    figure_cites       WARN  a figure some claim states, but not one this answer cites
    volunteered        WARN  an interview-only claim in an intro: say it when asked, not in the opening
    unsupported_system WARN  a known system no claim mentions, said as if used
    ai_tells           WARN  nobody says "leveraged" out loud
    answer_length      WARN  over two minutes (the intro: 100 seconds; a screen: 30 seconds), at 150 wpm
    i_not_we           WARN  an answer of 40+ words with more "we" than "I"
    resume_coverage    INFO  figures on the page the interviewer holds that the prep never says
"""
import re, sys
from pathlib import Path

import claimcheck as cc

WORDS_PER_MINUTE = 150
INTRO_HEADING = re.compile(r"\b(?:intro|introduction|about yourself|walk me through your background)\b", re.I)
STAGES = ("screen", "interview")


def limits(stage):
    if stage == "screen":
        return {"intro": 100, "answer": 30, "label": "over 30 seconds for a screen (the intro: 100 seconds)"}
    return {"intro": 100, "answer": 120, "label": "over two minutes (the intro: 100 seconds)"}


# ---------------------------------------------------------------- reading a file

def _split(s):
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def prose(body):
    """Markdown down to the words a listener hears: emphasis, links, list and quote markers go."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    s = s.replace("`", "")
    s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
    s = re.sub(r"(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)", r"\1\2", s, flags=re.M)
    lines = [re.sub(r"^\s*(?:[-*+•]|\d+[.)]|>)\s+", "", ln).strip() for ln in s.split("\n")]
    return re.sub(r"\s+", " ", " ".join(ln for ln in lines if ln)).strip()


def parse(md):
    """The answers in one file, with what the file's comments say about them."""
    src = md.replace("\r", "")
    first = re.search(r"<!--(.*?)-->", src, re.S)
    head = first.group(1) if first else ""
    # [ \t]*, never \s*: an empty "traces:" line must not run on into the next line
    traces = _split((re.search(r"^[ \t]*traces:[ \t]*(.*)$", head, re.M) or [None, ""])[1])
    resume = (re.search(r"^[ \t]*resume:[ \t]*(\S+)", head, re.M) or [None, None])[1]
    allowed = [x.strip() for m in re.finditer(r"<!--\s*untraced-ok:\s*(.*?)\s*-->", src, re.S)
               for x in m.group(1).split(",") if x.strip()]
    done = bool(re.search(r"<!--\s*done:", src))

    def comment(m):
        body = m.group(1)
        return f"\n@@CITES {body.strip()[len('cites:'):].strip()}\n" if body.strip().startswith("cites:") else ""
    marked = re.sub(r"<!--(.*?)-->", comment, src, flags=re.S)

    answers, cur, title = [], None, ""
    for line in marked.split("\n"):
        h = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if h:
            cur = {"heading": h.group(1).strip(), "body": [], "cites": [], "title": title}
            answers.append(cur)
            continue
        t = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if t:
            title = t.group(1).strip()
        if re.match(r"^#{1,6}\s", line):
            # any other heading ends the answer: a "### Notes" block is not part of what is said
            cur = None
            continue
        c = re.match(r"^@@CITES (.*)$", line)
        if c:
            if cur is not None:
                cur["cites"] += _split(c.group(1))
            continue
        if cur is not None:
            cur["body"].append(line)
    out = []
    for a in answers:
        text = prose("\n".join(a["body"]))
        if text:
            # "## Short" under "# Tell me about yourself" is an intro too
            intro = bool(INTRO_HEADING.search(a["heading"]) or INTRO_HEADING.search(a["title"]))
            out.append({"heading": a["heading"], "text": text, "cites": traces + a["cites"],
                        "own_cites": a["cites"], "intro": intro})
    return {"answers": out, "traces": traces, "resume": resume, "allowed": allowed, "done": done}


# ---------------------------------------------------------------- figures

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
UNITS = ("days?|weeks?|months?|quarters?|years?|hours?|hrs|minutes?|people|teams?|units?|requesters|requests|"
         "records|defects|releases|times|percent|dollars|points|items|tickets|users|suppliers|vendors|stories|"
         "tests|cases|sessions|sites|meetings")
_SPELLED = re.compile(r"\b((?:(?:" + "|".join(NUM_WORDS) + r"|hundred|thousand|million|a)(?:[- ]|\s+and\s+)?)+)"
                      r"(?=\s|[.,;:!?]|$)(\s+(?:" + UNITS + r")\b)?", re.I)
_DIGITS = re.compile(r"(?<![\w.$-])\$?(\d(?:[\d,]*\d)?(?:\.\d+)?)\+?(?:\s*(K|M)\b|s\b)?(?![\w-])")


def spelled_numbers(text):
    """Spelled numbers: 13 and up on their own, any size with a unit after it ("nine weeks")."""
    out = []
    for m in _SPELLED.finditer(text):
        words = [w for w in m.group(1).lower().replace("-", " ").split() if w != "and"]
        if not any(w in NUM_WORDS or w in ("hundred", "thousand", "million") for w in words):
            continue
        total = cur = 0
        for w in words:
            if w == "a":
                cur = cur or 1
            elif w in NUM_WORDS:
                cur += NUM_WORDS[w]
            elif w == "hundred":
                cur = (cur or 1) * 100
            else:
                total += (cur or 1) * (1000 if w == "thousand" else 1000000)
                cur = 0
        n = total + cur
        if not n or (n < 13 and not m.group(2)) or (n == 1 and len(words) == 1):
            continue
        said = m.group(0).strip()
        out.append({"said": said, "n": n, "at": m.start(), "end": m.start() + len(m.group(0).rstrip())})
    return out


def digit_figures(text):
    """Figures in digits. "$95K" is 95,000; a bare year is not a figure; "in 2021, and" is a year and a comma."""
    out = []
    for m in _DIGITS.finditer(text):
        raw = float(m.group(1).replace(",", ""))
        if 1990 <= raw <= 2035 and "," not in m.group(1) and not m.group(2) and raw.is_integer():
            continue
        n = raw * {"K": 1000, "M": 1000000}.get(m.group(2) or "", 1)
        if n < 2:
            continue
        out.append({"said": m.group(0).strip(), "n": n, "at": m.start(), "end": m.start() + len(m.group(0).rstrip())})
    return out


def figures_in(text):
    return digit_figures(text) + spelled_numbers(text)


def _forms(n):
    if float(n).is_integer():
        n = int(n)
        return {str(n), f"{n:,}"} | {w for w, v in NUM_WORDS.items() if v == n}
    return {repr(float(n)).rstrip("0").rstrip(".")}


def figure_in(f, facts):
    """Does any fact state the figure, in any of its forms ("1800", "1,800")?"""
    forms = _forms(f["n"])
    return any(re.search(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", str(fact), re.I) for fact in facts for x in forms)


def untraced_ok_spans(text, allowed):
    """An untraced-ok entry allows a figure only where the entry itself is said, as a phrase with its unit or
    symbol: "$95K" allows "$95K" and not "95+"; a bare "95" never allows "95+", "$95K" or "1,095"."""
    spans = []
    for e in allowed:
        src = re.sub(r"\\\s+|\s+", r"\\s+", re.escape(e.strip()))
        for m in re.finditer(r"(?<![\w$.,])" + src + r"(?![\w+%]|[.,]\d)", text, re.I):
            spans.append((m.start(), m.end()))
    return spans


def _allowed(f, spans):
    return any(f["at"] >= a and f["end"] <= b for a, b in spans)


# ---------------------------------------------------------------- the rules

NEGATION = re.compile(r"\b(?:no|not|never|none|nor|neither|without|zero)\b|n['’]t\b|\bworking toward|"
                      r"\bstudying for|\bpreparing for|\bpursuing\b", re.I)


def negated(text, start):
    """Is the phrase at start inside a negated or not-yet clause ("I haven't worked in SAP", "working toward
    a certification")? The clause runs back to the last comma or sentence stop, 60 characters at most."""
    clause = re.split(r"[.;:!?,]", text[:start])[-1][-60:]
    return bool(NEGATION.search(clause))


def phrase_hits(text, phrases):
    """Phrases said as fact: each whole-word hit that is not negated."""
    out, low = [], text.lower()
    for p in phrases:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(p.lower()) + r"(?![a-z0-9])", low):
            if not negated(low, m.start()):
                out.append(p)
                break
    return out


POSTING_REF = r"(?=\s+(?:role|position|opening|job|posting|req|requisition)\b)"


def strip_posting_refs(text, cfg):
    """"the Product Manager role" names the job applied for, not a title held."""
    alts = [re.escape(t) for t in cfg.get("titles_not_held", [])] + [cc.LEVEL_TITLE.pattern.strip("\\b")]
    return re.sub("(?:" + "|".join(alts) + ")" + POSTING_REF, " ", text) if alts else text


def i_and_we(text):
    i = len(re.findall(r"\b(?:I|I'm|I've|I'd|I'll|my|me)\b", text))
    we = len(re.findall(r"\b(?:we|we're|we've|we'd|our|us)\b", text, re.I))
    return i, we


def speaking(text):
    words = len(text.split())
    return words, round(words / WORDS_PER_MINUTE * 60)


def resume_figures(path):
    """Figures on the resume the interviewer holds: its bullets, citations removed."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    text = " ".join(cc.CITE.sub("", ln.strip()[2:]) for ln in lines if ln.strip().startswith(("- ", "* ")))
    return figures_in(text)


def check_file(path, cfg, stage=None):
    """[(status, check, detail)] for one file. status is FAIL, WARN, PASS or INFO."""
    path = Path(path)
    return check_text(path.read_text(encoding="utf-8-sig"), cfg, stage, base=path.parent)


def check_text(md, cfg, stage=None, base=None):
    """check_file on text; base is the directory a relative resume: path is read from."""
    base = Path(base) if base else Path.cwd()
    doc = parse(md)
    answers = doc["answers"]
    res = []

    def add(status, name, detail):
        res.append((status, name, detail))

    if not answers:
        add("FAIL", "answers_parsed", 'no answer text found: the convention is one "## <question>" heading per answer')
        return res
    add("PASS", "answers_parsed", f"{len(answers)} spoken answer(s)")
    claims = {c["id"]: c for c in cfg["claims"]}
    where = lambda a: f'"{a["heading"][:50]}"'

    hits = [f'{where(a)}: "{m}"' for a in answers for m in cc.people_management_hits(a["text"])]
    add("FAIL" if hits else "PASS", "people_management",
        " | ".join(hits) + " (managing people is not a claim he can make)" if hits else "nothing implies direct reports")

    hits = [f'{where(a)}: "{t}"' for a in answers for t in cc.held_title_hits(strip_posting_refs(a["text"], cfg), cfg)]
    add("FAIL" if hits else "PASS", "held_titles",
        " | ".join(hits) + ' (a title he does not hold; naming the posting, "the X role", is fine)' if hits
        else "no title claimed that he does not hold")

    hits = [f'{where(a)}: "{p}"' for a in answers for p in phrase_hits(a["text"], cfg.get("never_claim", []))]
    add("FAIL" if hits else "PASS", "never_claims",
        " | ".join(hits) + ' (never, in either voice; "I have no certification" is the truthful answer and passes)'
        if hits else f"none of the {len(cfg.get('never_claim', []))} never-claims said as fact")

    # voice tiers
    used, volunteered = [], []
    for c in claims.values():
        if c.get("voice", "written") == "written":
            continue
        for a in answers:
            m = cc.claim_hits(a["text"], c)
            if m:
                used.append(f"{where(a)}: {c['id']} ({c['voice']}{', only when asked' if c['voice'] == 'interview' else ''})")
                if c["voice"] == "interview" and a["intro"]:
                    volunteered.append(f'{where(a)}: "{m}" ({c["id"]})')
    if used:
        add("INFO", "voice", " | ".join(used))
    add("WARN" if volunteered else "PASS", "volunteered",
        " | ".join(volunteered) + " (interview-only: say it when asked, not in the opening)" if volunteered
        else "no interview-only claim in an intro")

    hits = []
    for c in claims.values():
        if not c.get("requires"):
            continue
        for a in answers:
            for s in re.split(r"(?<=[.!?])\s+", a["text"]):
                if cc.claim_hits(s, c) and not re.search(c["requires"], s, re.I):
                    hits.append(f'{where(a)}: {c["id"]} said without /{c["requires"]}/ ("{s[:70]}")')
    add("FAIL" if hits else "PASS", "spoken_estimate",
        " | ".join(dict.fromkeys(hits)) + " (the word is what keeps the figure true)" if hits
        else "every claim that needs a qualifier has it")

    # figures, against the claims each answer cites
    all_facts = [f for c in claims.values() for f in c.get("facts", [])]
    untraced, other, unknown, uncited, traced = [], [], [], [], 0
    for a in answers:
        for cid in a["cites"]:
            if cid not in claims:
                unknown.append(f"{where(a)}: {cid}")
        facts = [f for cid in a["cites"] if cid in claims for f in claims[cid].get("facts", [])]
        spans = untraced_ok_spans(a["text"], doc["allowed"])
        figs = [f for f in figures_in(a["text"]) if not _allowed(f, spans)]
        if figs and not a["cites"]:
            uncited.append(where(a))
        for f in figs:
            label = f"{where(a)}: {f['said']}"
            if a["cites"] and figure_in(f, facts):
                traced += 1
            elif not a["cites"] and figure_in(f, all_facts):
                traced += 1
            elif figure_in(f, all_facts):
                other.append(label)
            else:
                untraced.append(label)
    probs = []
    if untraced:
        probs.append("no claim states: " + " | ".join(dict.fromkeys(untraced)) + " (verify it and add a claim, or cut it)")
    if unknown:
        probs.append("cites a claim that does not exist: " + " | ".join(dict.fromkeys(unknown)))
    add("FAIL" if probs else "PASS", "figure_trace", " - ".join(probs) if probs else f"{traced} figure(s) traced")
    add("WARN" if other else "PASS", "figure_cites",
        " | ".join(dict.fromkeys(other)) + " (a claim states it, but not one this answer cites: cite the claim it comes from)"
        if other else "every figure is in a claim its answer cites")
    if uncited:
        add("INFO", "uncited", " | ".join(uncited) + ": no traces or cites, so checked against every claim "
            "(a common number always matches something)")

    supported = {f.lower() for f in all_facts}
    hits = [f'{where(a)}: {s}' for a in answers
            for s in phrase_hits(a["text"], [s for s in cfg.get("known_systems", []) if s.lower() not in supported])]
    add("WARN" if hits else "PASS", "unsupported_system",
        " | ".join(hits) + " (no claim mentions it; a negated mention is fine)" if hits
        else "every system named is one a claim mentions")

    if doc["resume"]:
        rp = (base / doc["resume"]) if not Path(doc["resume"]).is_absolute() else Path(doc["resume"])
        if not rp.exists():
            rp = cc.ROOT / doc["resume"]
        try:
            page = resume_figures(rp)
            said = [f for a in answers for f in figures_in(a["text"])]
            missing = list(dict.fromkeys(p["said"] for p in page if not any(s["n"] == p["n"] for s in said)))
            add("INFO", "resume_coverage", f"against {doc['resume']}: " + (
                f"the page says {', '.join(missing)} and the prep never does (be ready to be asked)" if missing
                else "every figure on the page is in the prep"))
        except OSError as e:
            add("WARN", "resume_coverage", f"cannot read {doc['resume']} ({e})")

    hits = [f'{where(a)}: "{t}"' for a in answers for t in cc.ai_tell_hits(a["text"])]
    add("WARN" if hits else "PASS", "ai_tells", " | ".join(hits) + " (nobody says these out loud)" if hits
        else "none of the AI-tell words")

    lim = limits(stage)
    timing = [(a, *speaking(a["text"])) for a in answers]
    long = [f"{where(a)}: {s}s ({w} words)" for a, w, s in timing if s > (lim["intro"] if a["intro"] else lim["answer"])]
    every = " | ".join(f"{where(a)} {s}s" for a, w, s in timing)
    if long:
        add("WARN", "answer_length", " | ".join(long) + f" ({lim['label']}; start short, they will ask for more). "
            f"All answers at {WORDS_PER_MINUTE} wpm: {every}")
    else:
        add("INFO", "answer_length", f"at {WORDS_PER_MINUTE} wpm{f' (stage {stage})' if stage else ''}: {every}")

    heavy = []
    for a, w, _ in timing:
        i, we = i_and_we(a["text"])
        if w >= 40 and we > i:
            heavy.append(f"{where(a)}: we {we} / I {i}")
    add("WARN" if heavy else "PASS", "i_not_we", " | ".join(heavy) + ' (say what you did; "we" hides it)' if heavy
        else 'no answer leans on "we" over "I"')

    if doc["done"]:
        warns = [r for r in res if r[0] == "WARN"]
        res = [r for r in res if r[0] != "WARN"]
        if warns:
            res.append(("INFO", "done", f"round over: {len(warns)} warning(s) not shown ({', '.join(r[1] for r in warns)})"))
    return res


def files_for(target):
    p = Path(target)
    if p.is_dir():
        return sorted(f for f in p.glob("*.md") if f.name.upper() not in ("INDEX.MD", "README.MD"))
    return [p]


def run(target, cfg, stage=None, out=None):
    """Check a file or a directory and print the report. Exit code: 0 clean, 1 on any FAIL, 2 on bad input."""
    out = out or sys.stdout
    if stage is not None and stage not in STAGES:
        print(f"claimcheck: --stage takes one of {', '.join(STAGES)}", file=sys.stderr)
        return 2
    files = files_for(target)
    if not files or not all(f.exists() for f in files):
        missing = next((str(f) for f in files if not f.exists()), target)
        print(f"claimcheck: file not found: {missing}", file=sys.stderr)
        return 2
    fails = warns = 0
    for f in files:
        try:
            res = check_file(f, cfg, stage)
        except (OSError, UnicodeDecodeError) as e:
            print(f"claimcheck: cannot read input ({e})", file=sys.stderr)
            return 2
        print(f"\n{f.as_posix()}", file=out)
        for status, name, detail in res:
            print(f"  {status:<5} {name:<19} {detail}", file=out)
        fails += sum(r[0] == "FAIL" for r in res)
        warns += sum(r[0] == "WARN" for r in res)
    verdict = "BLOCKED, fix the FAIL lines above" if fails else "every spoken figure and phrase traces to a verified claim"
    print(f"\n{len(files)} file(s): {fails} fail, {warns} warn: {verdict}", file=out)
    return 1 if fails else 0
