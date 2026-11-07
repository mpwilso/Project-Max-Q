"""claimcheck.py - block a resume that says anything the candidate's verified claims don't support.

Every bullet on the resume must cite at least one claim, as [claim-id], from the claims file. The
bullet is then checked against the facts of the claims it cites:

    figures   every number in the bullet must be one those claims state, with the same unit and scale
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
    metrics             a figure with a unit ($1.2M, 40%, 3x, 400+ users, 9 working days) that no claim
                        states with the same value, unit and scale: a bullet's cited claims, or any
                        written claim on an uncited line. Years, versions and quarters are not metrics.
    one page            more non-blank lines than max_lines in the claims file (50 when unset); the
                        first line over the budget is the one named

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
MAX_LINES = 50   # non-blank lines on one page, when the claims file sets no max_lines


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
    budget = cfg.get("max_lines", MAX_LINES)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        out.append(f"max_lines must be a whole number of lines above zero, not {budget!r}")
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
    # starts only where a run of digits starts, so a long run is read once, not once per digit
    m = re.search(r"(?<![\d,.])\d[\d,.]*\+?\s*(?:[a-z]+\s+)?or so\b", low)
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


# A figure is read as (value, unit, scale), and a fact supports it only when the fact reads as the same
# three: "$2.4m" supports "$2.4 million", "40%" supports "40 percent", and a bare "3" supports "3 regions"
# but never "$3M", "3%" or "3x". The unit is a currency sign, "%", "pp" (percentage points), "x" (a
# multiplier), "count" (a bare number, or a number of something) or "" (a scaled number and nothing else,
# "2.4M", which a money fact of the same value and scale also supports); the scale is "", "k", "m" or "b".
# A range ("10-20%", "$1-2M") is checked at both ends. A plus or a sign changes nothing.
# A figure with a unit is a metric: money, a percentage, a multiplier, a scaled number, a number with a
# plus, or a count of a noun below. A bare number on a bullet is already held to the cited claims; the
# metric check names the unit too, and reaches lines nobody cites, such as a summary line. Never a metric:
# a year (1900 to 2099, four digits, with no currency or percent), so "2021 to present" and "In 2023 the
# team" pass; a version (3.12 after Python, version, v or release, 2.0 before release, or 3.12.1); a
# quarter (Q3). The number has a fixed shape, and cannot start after a comma or a dot, so a long run of
# "1,1,1" is read in one pass.
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.\d+)?"
NUMBER = re.compile(rf"(?<![\w.,]){_NUM}(?!\d)")
_CURRENCY = re.compile(r"([$€£])\s?$")
_SCALE = re.compile(r"\s?(thousand|million|billion|bn|mm|k|m|b)(?![a-z])", re.I)
_SCALES = {"thousand": "k", "k": "k", "million": "m", "mm": "m", "m": "m", "billion": "b", "bn": "b", "b": "b"}
_RANGE = re.compile(r"\s?[-\N{EN DASH}]\s?([$€£]\s?)?(?=\d)")       # a hyphen or an en dash between the ends
# the nouns a count may name; filler words ("9 working days") reach only the first list
METRIC_NOUNS = ("users?", "requesters?", "customers?", "clients?", "accounts?", "requests?", "tickets?",
                "teams?", "people", "employees?", "engineers?", "developers?", "analysts?", "staff",
                "records?", "rows?", "suppliers?", "vendors?", "transactions?", "orders?", "invoices?",
                "units?", "countr(?:y|ies)", "regions?", "markets?", "sites?", "stores?", "locations?",
                "offices?", "hours?", "days?", "weeks?", "months?", "years?", "sprints?", "fte", "headcount")
DIRECT_NOUNS = ("systems?", "integrations?", "releases?", "projects?", "programs?", "products?", "features?",
                "quarters?")
_FILLER_STOP = ("to|for|the|a|an|all|our|of|in|on|at|by|from|after|before|with|and|or|per|into|across|over|"
                "under|since|during|its|their|each|every|these|those|this|that|his|her|my|your|than|as|out|"
                "up|via|through|between|among|within|without|about|around|is|are|was|were|be|been|it|we|"
                "they|i|he|she")
_UNIT = re.compile(
    r"\s?(?P<pp>percentage points?(?![a-z]))"
    r"|\s?(?P<pct>%|percent(?![a-z])|pct(?![a-z]))"
    r"|\s?(?P<x>[x×](?![a-z])(?!\s?\d))"
    r"|\s+(?P<times>times\s+(?:more|faster|higher|larger|bigger|greater|the|as|over)\b)"
    rf"|\s+(?P<count>(?:(?!(?:{_FILLER_STOP})\b)[a-z][a-z-]*\s+){{0,2}}(?:{'|'.join(METRIC_NOUNS)})(?![a-z])"
    rf"|(?:{'|'.join(DIRECT_NOUNS)})(?![a-z]))", re.I)
_TIMES_FACT = re.compile(r"\s+times(?![a-z])", re.I)          # a fact may say "3 times" with nothing after
VERSION_BEFORE = re.compile(r"\b(?:version|ver|v|release[ds]?|releasing|python|java|node(?:\.?js)?|php|ruby|"
                            r"golang|go|rust|perl|scala|kotlin|swift|ios|android|windows|macos|ubuntu|angular|"
                            r"react|vue|django|rails|spring|\.net|tls|ssl|http|oauth|"
                            r"(?:upgrad|migrat|updat|bump)(?:e|ed|es|ing)\s+to)\s+$", re.I)
_DIRECT = re.compile(rf"\b(?:{'|'.join(DIRECT_NOUNS)})$", re.I)
VERSION_AFTER = re.compile(r"\.\d|\s+(?:release|version|update|upgrade|beta|sdk|api|launch)\b", re.I)


def figures(text, fact=False):
    """Every number in text as a figure: a dict with its text, its ends as (value, unit, scale), the
    numbers as written, whether it is a metric, and whether it is a version. fact=True reads a fact,
    where "3 times" on its own is a multiplier. One pass, left to right; a range consumes its far end."""
    from decimal import Decimal
    out, pos = [], 0
    for m in NUMBER.finditer(text):
        if m.start() < pos:
            continue
        cur = _CURRENCY.search(text, max(0, m.start() - 2), m.start())
        start = cur.start() if cur else m.start()
        sign = cur.group(1) if cur else ""
        nums, scales, i = [m.group(0)], [], m.end()
        sc = _SCALE.match(text, i)
        scales.append(_SCALES[sc.group(1).lower()] if sc else "")
        i = sc.end() if sc else i
        rg = _RANGE.match(text, i)
        far = NUMBER.match(text, rg.end()) if rg else None
        if far:
            sign = sign or (rg.group(1) or "").strip()
            nums.append(far.group(0))
            i = far.end()
            sc = _SCALE.match(text, i)
            scales.append(_SCALES[sc.group(1).lower()] if sc else "")
            i = sc.end() if sc else i
            scales[0] = scales[0] or scales[1]
        plus = text.startswith("+", i)
        i += plus
        u = _UNIT.match(text, i) or (_TIMES_FACT.match(text, i) if fact else None)
        kind = None
        if u:
            kind = u.lastgroup or "x"           # the fact-only "times" pattern has no group
            kind = {"pp": "pp", "pct": "%", "x": "x", "times": "x", "count": "count"}[kind]
            i = u.end()
        unit = kind if kind in ("pp", "%", "x") else sign or ("count" if kind or not any(scales) else "")
        year = (not sign and kind not in ("pp", "%", "x")
                and all(re.fullmatch(r"(?:19|20)\d\d", n) for n in nums[:1]))
        # a version names a release, never a percentage or a count of people, days or records
        versionable = kind is None or (kind == "count" and _DIRECT.search(u.group(0)))
        version = ("." in m.group(0) and not sign and bool(versionable)
                   and bool(VERSION_BEFORE.search(text, max(0, m.start() - 40), m.start())
                     or VERSION_AFTER.match(text, m.end())))
        metric = bool(sign or any(scales) or kind or plus) and not year and not version
        if not metric:
            i = m.end() if len(nums) == 1 else far.end()
        ends = [(Decimal(n.replace(",", "")), unit, s) for n, s in zip(nums, scales)]
        out.append({"text": re.sub(r"\s+", " ", text[start:i]).strip(), "ends": ends, "nums": nums,
                    "metric": metric, "version": version})
        pos = i
    return out


def fact_figures(facts):
    """Every (value, unit, scale) the facts state, versions aside."""
    return {e for f in facts for fig in figures(f, fact=True) if not fig["version"] for e in fig["ends"]}


def supported(end, known):
    """Whether a fact states this end of a figure: the same value, unit and scale. A scaled number with no
    unit ("2.4M") and a count of the same scale support each other, and a money fact supports the former."""
    value, unit, scale = end
    if end in known:
        return True
    if unit in ("", "count"):
        alts = ("", "count") + (("$", "€", "£") if unit == "" else ())
        return any((value, u, scale) in known for u in alts)
    return False


def metric_hits(text, facts):
    """The figures with a unit in text that none of facts states, at either end of a range."""
    known = fact_figures(facts)
    out = [f["text"] for f in figures(text)
           if f["metric"] and not all(supported(e, known) for e in f["ends"])]
    return list(dict.fromkeys(out))


def bare_number_hits(text, facts):
    """The numbers in text that the facts don't state, as written. A number inside a figure the facts
    support is stated; any other needs a bare fact of the same value. A version is not a figure."""
    known = fact_figures(facts)
    out = []
    for f in figures(text):
        if f["version"] or (f["metric"] and all(supported(e, known) for e in f["ends"])):
            continue
        out += [n for n, e in zip(f["nums"], f["ends"]) if (e[0], "count", "") not in known]
    return out


# ---------------------------------------------------------------- the resume

def check_bullet(line, claims, cfg):
    """Return the reasons a bullet is blocked; an empty list means it passes."""
    reasons = []
    cited = CITE.findall(line)
    prose = CITE.sub("", line)
    text = prose.lower()
    if not cited:
        return (["cites no claim"]
                + [f"metric \"{m}\" is not in any written claim" for m in metric_hits(prose, written_facts(cfg))]
                + prose_reasons(prose, cfg))
    unknown = [c for c in cited if c not in claims]
    if unknown:
        reasons.append("cites unknown claim(s): " + ", ".join(unknown))
    facts = {f for c in cited if c in claims for f in claims[c]["facts"]}
    for n in bare_number_hits(prose, facts):
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
    reasons += [f"metric \"{m}\" is not in the cited claim(s)" for m in metric_hits(prose, facts)]
    return reasons + prose_reasons(prose, cfg)


def written_facts(cfg):
    """What any line may state without citing a claim: the facts of every written claim. A spoken or
    interview claim's facts are left out, since they are never printed."""
    return {f for c in cfg.get("claims", []) if c.get("voice", "written") == "written" for f in c["facts"]}


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


def page_reason(shown, budget):
    """The one-page rule, for the first non-blank line past the budget; None for every other line."""
    if shown == budget + 1:
        return f"over one page: non-blank line {shown}, past the budget of {budget} (max_lines in the claims file)"
    return None


def check(resume_text, cfg):
    """Check every bullet and role heading, and every other line for the page-wide rules. Returns a list
    of (line_no, line, reasons); a line that is neither a bullet nor a role heading is listed only when
    something is wrong with it."""
    claims = {c["id"]: c for c in cfg["claims"]}
    held = {t.lower() for t in cfg.get("titles_held", [])}
    uncited = written_facts(cfg)
    budget = cfg.get("max_lines", MAX_LINES)
    results, shown = [], 0
    for n, raw in enumerate(resume_text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        shown += 1
        page = [r] if (r := page_reason(shown, budget)) else []
        if line.startswith(("- ", "* ")):
            results.append((n, line, page + dash_hits(line) + check_bullet(line[2:], claims, cfg)))
        elif (m := ROLE_HEADING.match(line)):
            title = m.group("title").strip()
            results.append((n, line, page + dash_hits(line)
                            + ([] if title.lower() in held else [f"title not held: {title}"])))
        else:
            reasons = page + dash_hits(line)
            if not line.startswith("#"):
                reasons += [f"metric \"{m}\" is not in any written claim" for m in metric_hits(line, uncited)]
                reasons += prose_reasons(line, cfg)
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
