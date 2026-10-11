"""claimcheck.py - block a resume that says anything the candidate's verified claims don't support.

Every bullet on the resume must cite at least one claim, as [claim-id], from the claims file. The
bullet is then checked against the facts of the claims it cites:

    figures   every number in the bullet must be one those claims state
    systems   every named system or tool must be one those claims support
    never     nothing from the never-claim list (people management, certifications he lacks, ...)
    titles    every role heading must be a title the candidate actually held

and against rules that hold for any candidate, on every line of the page:

    people management   "managed 6 analysts", "direct reports", "reported to me", "team of four", pods
    hedges              about, roughly, some 400, upward of, ~ or the approx sign, "about half", "or so"
    AI-tell words       leveraged, spearheaded, cutting edge (with or without the hyphen), robustly ...
    titles not held     a title-cased title from titles_not_held, anywhere on the page (case-sensitive)
    voice               a claim whose voice is spoken or interview is said aloud, never printed
    dashes              em-dashes, U+2015 and "--", and invisible characters (zero-width, soft hyphen)

A single failing line blocks the whole resume (exit code 1). The report names the line and the reason,
so the fix is to change the wording or add a verified claim, never to argue with the check. A missing
or unreadable input file, or a claims file with a malformed pattern, exits 2 with a one-line error.

    python claimcheck.py examples/john_doe/resume.md
    python claimcheck.py examples/john_doe/resume_blocked.md      # shows what gets blocked
    python claimcheck.py --spoken examples/john_doe/prep/         # interview prep: see spokencheck.py
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLAIMS = ROOT / "examples" / "john_doe" / "claims.json"

CITE = re.compile(r"\[([a-z0-9-]+)\]")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
ROLE_HEADING = re.compile(r"^###\s+(?P<title>[^·|]+?)\s*[·|]")
VOICES = ("written", "spoken", "interview")


def has_phrase(text, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


# ---------------------------------------------------------------- the claims file

def config_problems(cfg):
    """What is wrong with a claims file, one line per problem. Each claim may carry:
    voice     written (the default: page and aloud), spoken (aloud only) or interview (aloud, when asked)
    patterns  case-insensitive regexes that recognise the claim in prose; a spoken or interview claim,
              and one with requires, needs them, since nothing else can tell it is being said
    requires  a regex the same sentence must also match whenever the claim is said ("estimated")
    A JSON string "\\b" is a backspace character, not a word boundary, so a pattern holding a control
    character is refused: it would compile, match nothing, and never fire."""
    out = []
    for c in cfg.get("claims", []):
        cid = c.get("id", "?")
        voice = c.get("voice", "written")
        if voice not in VOICES:
            out.append(f"claim {cid}: voice must be one of {', '.join(VOICES)}, not {voice!r}")
        pats = c.get("patterns") or []
        if (voice != "written" or c.get("requires")) and not pats:
            out.append(f"claim {cid}: a {voice} claim, or one with requires, needs patterns that recognise it")
        for p in pats + ([c["requires"]] if c.get("requires") else []):
            if re.search(r"[\x00-\x1f\x7f]", p):
                out.append(f"claim {cid}: pattern {p!r} holds a control character "
                           "(in JSON write \"\\\\b\" for a word boundary; \"\\b\" is a backspace)")
                continue
            try:
                re.compile(p)
            except re.error as e:
                out.append(f"claim {cid}: pattern {p!r} is not a valid regex ({e})")
    return out


def claim_hits(text, claim):
    """The first match of any of the claim's patterns in text, or None."""
    for p in claim.get("patterns") or []:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0).strip()
    return None


# ---------------------------------------------------------------- rules that hold for any candidate
# Each takes the line as written (case kept) and returns the matched phrases.

NUM_WORD = "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
# a sentence with a negation in it is the truthful answer ("I have no direct reports") and passes
_NOT_NEGATED = (r"(?:^|[.!?]\s+)(?![^.!?]*(?:\b(?:no|not|never|nobody|nothing|none|zero|without|any)\b"
                r"|n['’]t\b))[^.!?]*?")
PEOPLE_MANAGEMENT = [
    r"\bmanaged (?:a|an|the|my) [^.]{0,20}\bteam\b(?!['’]s)",
    r"\bled a team of\b",
    r"\bmy team of\b",
    rf"\bteam of (?:\d+|{NUM_WORD})\b",
    r"\b(?:managed|supervised|oversaw|headed|hired)\b[^.]{0,40}?\b(?:engineers|developers|analysts|reports|staff)\b",
    r"\b(?:supervised|oversaw|headed)\b[^.]{0,30}?\bteam\b",
    _NOT_NEGATED + r"\bdirect reports?\b",
    # the reporting line said from the other end
    _NOT_NEGATED + (r"(?:\b(?:report(?:s|ed|ing)?|answer(?:s|ed|ing)?) (?:directly )?to me\b"
                    r"|\bmy (?:direct )?reports\b(?! (?:on|to|for|about|of|in|from|that|which)\b))"),
    # a pod you lead or manage is a team you manage
    _NOT_NEGATED + (r"(?:\b(?:manag(?:e|ed|es|ing)|supervis(?:e|ed|es|ing)|overs(?:ee|ees|eeing|aw)"
                    r"|head(?:ed|ing|s)?|lead(?:s|ing)?|led|ran|run(?:s|ning)?)\s+(?:a|an|the|my|our)\s+(?:\w+\s+)?pod\b"
                    rf"|\bpod of (?:\d+|{NUM_WORD})\b|\bmy pod\b)"),
]


def people_management_hits(text):
    out = []
    for p in PEOPLE_MANAGEMENT:
        m = re.search(p, text, re.I)
        if m:
            out.append(re.sub(r"^[.!?\s]+", "", m.group(0)).strip())
    return list(dict.fromkeys(out))


HEDGE_ALWAYS = ["estimated", "approximately", "approx.", "roughly", "circa"]
HEDGE_BEFORE_FIGURE = ["about", "around", "nearly", "up to", "as many as", "more than"]
HEDGE_DIRECT = ["over", "almost", "close to", "at least", "upwards of", "upward of", "north of", "some"]
# "handed over 3 systems" is a particle and "over 6 months" a span, not a hedged figure
HEDGE_OVER_PARTICLE = re.compile(r"\b(?:hand|hands|handed|handing|carry|carried|roll|rolled|pass|passed|turn|"
                                 r"turned|take|took|start|started|win|won|left|go|went|move|moved)\s+$", re.I)
HEDGE_OVER_DURATION = re.compile(r"^[\d,.]+\+?\s*(?:day|week|month|quarter|year|sprint|hour|minute)s?\b", re.I)
# "a talk about the 3 units" is a preposition, not an approximation
HEDGE_DETERMINERS = "the|our|its|their|this|these|those|his|her|my|your|each|every"


def hedge_hits(text):
    out, low = [], text.lower()
    for h in HEDGE_ALWAYS:
        if re.search(r"\b" + re.escape(h), low):
            out.append(h)
    for h in HEDGE_BEFORE_FIGURE:
        prep = rf"(?!(?:{HEDGE_DETERMINERS})\b)" if h in ("about", "around") else ""
        m = re.search(rf"\b{h}\b\s+{prep}(?:[a-z]+\s+){{0,2}}(?:[$€£]\s*)?[\d~]", low)
        if m:
            out.append(m.group(0).strip())
    for h in HEDGE_DIRECT:
        for m in re.finditer(rf"\b{h}\b\s+(?:[$€£]\s*)?[\d~]", low):
            if h == "over" and (HEDGE_OVER_PARTICLE.search(low[:m.start()])
                                or HEDGE_OVER_DURATION.match(low[m.end() - 1:])):
                continue
            out.append(m.group(0).strip())
            break
    if re.search(r"[~≈∼]\s*\d", text):
        out.append("~ before a figure")
    m = re.search(r"\b(?:about|almost|nearly|roughly|around|some)\s+(?:half|a third|a quarter|two thirds|"
                  r"three quarters|double|triple)\b", low)
    if m:
        out.append(m.group(0))
    m = re.search(r"\d[\d,.]*\+?\s*(?:[a-z]+\s+)?or so\b", low)
    if m:
        out.append(m.group(0))
    return list(dict.fromkeys(out))


AI_TELLS = ["spearheaded", "spearhead", "leveraged", "leverage", "leveraging", "dynamic", "results-driven",
            "synergy", "synergies", "passionate", "proven track record", "seasoned", "cutting-edge",
            "innovative solutions", "utilized", "utilize", "thought leader", "best-in-class", "world-class",
            "game-changing", "game changer", "holistic", "holistically", "delve", "robust", "robustly",
            "seamless", "seamlessly", "empowered", "empowering", "visionary", "proficient", "detail-oriented",
            "foster", "fostered", "fostering", "comprehensive", "streamline", "streamlined", "streamlining",
            "facilitate", "facilitated", "facilitating", "pivotal", "showcase", "showcased", "showcasing"]
# the same lexicon as stems, because a word list misses an inflection ("utilizing")
AI_TELL_STEMS = [r"\butiliz(?:e|es|ed|ing|ation)\b", r"\bspearhead(?:s|ed|ing)?\b", r"\bleverag(?:e|es|ed|ing)\b",
                 r"\bfoster(?:s|ed|ing)?\b", r"\bstreamlin(?:e|es|ed|ing)\b", r"\bfacilitat(?:e|es|ed|ing)\b",
                 r"\bshowcas(?:e|es|ed|ing)\b", r"\bsynerg(?:y|ies|istic)\b", r"\bpassion(?:ate)?\b"]


def ai_tell_hits(text):
    out = []
    for t in AI_TELLS:
        # the hyphen is optional: "world class", and a "cutting edge" whose hyphen was lost, are the same word
        rx = r"\b" + r"[- ]?".join(re.escape(part) for part in re.split(r"[- ]", t)) + r"\b"
        m = re.search(rx, text, re.I)
        if m:
            out.append(m.group(0))
    for rx in AI_TELL_STEMS:
        m = re.search(rx, text, re.I)
        if m:
            out.append(m.group(0))
    return list(dict.fromkeys(x.lower() for x in out))


DASHES = re.compile(r"[\u2014\u2015]|(?<=\S)\s?--\s?(?=\S)")
INVISIBLE = re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad]")
TABLE_RULE = re.compile(r"^[\s|:-]+$")


def dash_hits(line):
    """An em-dash (U+2014 or U+2015, or "--" standing in for one) and any invisible character."""
    out = []
    if not TABLE_RULE.match(line) and DASHES.search(line):
        out.append("em-dash: use a comma, a colon or a full stop")
    n = len(INVISIBLE.findall(line))
    if n:
        out.append(f"{n} invisible character(s) (zero-width or soft hyphen): hidden text reads as stuffing")
    return out


# a title at a level above the one held, whatever the candidate's file lists
LEVEL_TITLE = re.compile(r"\b(?:Senior|Sr\.?|Staff|Principal|Lead|Group)\s+(?:Technical\s+)?"
                         r"(?:Product|Program|Project)\s+(?:Manager|Owner)\b")


def held_title_hits(text, cfg):
    """Title-cased titles the candidate does not hold. Case-sensitive on purpose: "a product manager's
    view" describes work, "Product Manager" asserts a title. The titles held are removed first."""
    for t in cfg.get("titles_held", []):
        text = text.replace(t, " ")
    out = [t for t in cfg.get("titles_not_held", []) if re.search(r"\b" + re.escape(t) + r"\b", text)]
    out += [m.group(0) for m in LEVEL_TITLE.finditer(text)]
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- the resume

def check_bullet(line, claims, cfg):
    """Return the reasons a bullet is blocked; an empty list means it passes."""
    reasons = []
    cited = CITE.findall(line)
    prose = CITE.sub("", line)
    text = prose.lower()
    if not cited:
        return ["cites no claim"] + prose_reasons(prose, cfg)
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
    for c in cited:
        if c in claims and claims[c].get("voice", "written") != "written":
            reasons.append(f"cites {c}, a {claims[c]['voice']}-only claim: say it, never print it")
    return reasons + prose_reasons(prose, cfg)


def prose_reasons(text, cfg):
    """The rules that hold for any candidate, on any line of prose."""
    out = [f"people management: \"{m}\"" for m in people_management_hits(text)]
    out += [f"hedge: \"{m}\"" for m in hedge_hits(text)]
    out += [f"AI-tell word: \"{m}\"" for m in ai_tell_hits(text)]
    out += [f"title not held: \"{t}\"" for t in held_title_hits(text, cfg)]
    for c in cfg.get("claims", []):
        if c.get("voice", "written") != "written" and (m := claim_hits(text, c)):
            out.append(f"states {c['id']} (\"{m}\"), a {c['voice']}-only claim: say it, never print it")
    return out


def check(resume_text, cfg):
    """Check every bullet and role heading, and every other line for the page-wide rules. Returns a list
    of (line_no, line, reasons); a line that is neither a bullet nor a role heading is listed only when
    something is wrong with it."""
    claims = {c["id"]: c for c in cfg["claims"]}
    held = {t.lower() for t in cfg.get("titles_held", [])}
    results = []
    for n, raw in enumerate(resume_text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            results.append((n, line, dash_hits(line) + check_bullet(line[2:], claims, cfg)))
        elif (m := ROLE_HEADING.match(line)):
            title = m.group("title").strip()
            results.append((n, line, dash_hits(line) + ([] if title.lower() in held else [f"title not held: {title}"])))
        elif line:
            reasons = dash_hits(line) + ([] if line.startswith("#") else prose_reasons(line, cfg))
            if reasons:
                results.append((n, line, reasons))
    return results


def load_claims(path):
    """(claims, None), or (None, a one-line error) for a file that is missing, unreadable or malformed."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as e:
        return None, f"claimcheck: file not found: {e.filename}"
    except json.JSONDecodeError as e:
        return None, f"claimcheck: {path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}"
    except (OSError, UnicodeDecodeError) as e:
        return None, f"claimcheck: cannot read input ({e})"
    probs = config_problems(cfg)
    if probs:
        return None, f"claimcheck: {path}: " + "; ".join(probs)
    return cfg, None


def main():
    ap = argparse.ArgumentParser(description="Block resume lines, or spoken answers, the verified claims don't support.")
    ap.add_argument("resume", nargs="?")
    ap.add_argument("--claims", default=str(CLAIMS))
    ap.add_argument("--spoken", metavar="FILE_OR_DIR",
                    help="check interview prep, story files or a mock transcript instead (see spokencheck.py)")
    ap.add_argument("--stage", choices=("screen", "interview"),
                    help="with --spoken: a screen holds every answer but the intro to 30 seconds")
    a = ap.parse_args()
    if not a.resume and not a.spoken:
        ap.error("give a resume file, or --spoken <file or directory>")
    cfg, err = load_claims(a.claims)
    if err:
        print(err, file=sys.stderr)
        return 2
    if a.spoken:
        import spokencheck
        return spokencheck.run(a.spoken, cfg, stage=a.stage)
    try:
        resume = Path(a.resume).read_text(encoding="utf-8-sig")
    except FileNotFoundError as e:
        print(f"claimcheck: file not found: {e.filename}", file=sys.stderr)
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
