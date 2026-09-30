"""Generate the Max Q logo: a ring with a rocket breaking through it, plus a geometric wordmark.

Writes mark-*.svg and lockup-*.svg next to this file, one per GitHub theme. The animated files carry
their motion as CSS inside the SVG, so they play in a README with no scripts; the burn stops for
anyone whose system asks for reduced motion. Run: python docs/brand/build_brand.py
"""
from pathlib import Path

OUT = Path(__file__).parent

THEMES = {
    # ship/ring, wordmark, flame
    "light": {"mark": "#1668E3", "word": "#1F2328", "flame": "#FF6B1A"},
    "dark": {"mark": "#4B9BFF", "word": "#E6EDF3", "flame": "#FF7F2A"},
}

# Rocket, drawn pointing up (-y), centered on the ring's center. Panels are separate shapes with gaps.
SHIP = [
    "M0,-50 C7,-42 10,-32 10,-18 L-10,-18 C-10,-32 -7,-42 0,-50 Z",                    # nose cone
    "M-10,-15 H10 V24 H-10 Z M-4.5,-4 a4.5,4.5 0 1,0 9,0 a4.5,4.5 0 1,0 -9,0 Z",       # body + window
    "M-13,4 L-24,22 V32 L-13,26 Z",                                                    # left fin
    "M13,4 L24,22 V32 L13,26 Z",                                                       # right fin
    "M-6,27 H6 L8,33 H-8 Z",                                                           # nozzle
]
FLAME = "M-6,36 L0,55 L6,36 Z"
REST = 2  # rest offset toward the tail: the nose breaks the ring and the fins reach it

MOTION_CSS = {
    "static": "",
    # Mostly still; the ship nudges out of the ring on a short burn, holds, settles back.
    "animated": (
        ".fly{animation:push 6s cubic-bezier(.45,0,.2,1) infinite}"
        "@keyframes push{0%,60%{transform:translate(0,0)}72%,84%{transform:translate(0,-11px)}100%{transform:translate(0,0)}}"
        ".flame{transform-box:fill-box;transform-origin:50% 0;animation:burn 6s linear infinite}"
        "@keyframes burn{0%,58%{opacity:0;transform:scaleY(.2)}62%{opacity:1;transform:scaleY(1)}"
        "66%{transform:scaleY(.65)}70%{transform:scaleY(1)}74%{opacity:1;transform:scaleY(.55)}"
        "80%,100%{opacity:0;transform:scaleY(.2)}}"
    ),
}
REDUCED = "@media (prefers-reduced-motion:reduce){.fly,.flame{animation:none}}"


def mark_body(c, ox=0, oy=0, uid="m"):
    """The ring + ship, in a 120x120 box at (ox, oy)."""
    ship = "".join(f'<path d="{d}"/>' for d in SHIP)
    frame = f'transform="translate({ox + 60} {oy + 60}) rotate(45) translate(0 {REST}) scale(1.12)"'
    return (
        f'<defs><g id="{uid}s" fill-rule="evenodd">{ship}</g>'
        f'<mask id="{uid}k" maskUnits="userSpaceOnUse" x="{ox - 40}" y="{oy - 40}" width="200" height="200">'
        f'<rect x="{ox - 40}" y="{oy - 40}" width="200" height="200" fill="#fff"/>'
        f'<g {frame}><g class="fly"><use href="#{uid}s" fill="#000" stroke="#000" stroke-width="7" stroke-linejoin="round"/></g></g>'
        f"</mask></defs>"
        f'<circle cx="{ox + 60}" cy="{oy + 60}" r="42" fill="none" stroke="{c["mark"]}" stroke-width="9" mask="url(#{uid}k)"/>'
        f'<g {frame}><g class="fly">'
        f'<path class="flame" d="{FLAME}" fill="{c["flame"]}" opacity="0"/>'
        f'<use href="#{uid}s" fill="{c["mark"]}"/></g></g>'
    )


# Wordmark "MAX Q": extended geometric capitals, cap height 40 (y 40..80), stroke weight about 8.
GLYPHS = {
    "M": "M0,80 V40 H9 L22,58 L35,40 H44 V80 H36 V54 L22,72 L8,54 V80 Z",
    "A": "M0,80 L18,40 H26 L44,80 H35 L22,51.1 L9,80 Z",  # crossbar-less, as on launch vehicles
    "X": "M0,40 H11 L22,53.33 L33,40 H44 L27.5,60 L44,80 H33 L22,66.67 L11,80 H0 L16.5,60 Z",
}


def q_glyph(x, c):
    # Rounded-square Q; its tail cuts the bowl with the same clearance the ship cuts the ring.
    tail = f"M{x + 23},61 L{x + 31},61 L{x + 49},85 L{x + 41},85 Z"
    return (
        f'<mask id="qk" maskUnits="userSpaceOnUse" x="{x - 10}" y="30" width="70" height="70">'
        f'<rect x="{x - 10}" y="30" width="70" height="70" fill="#fff"/>'
        f'<path d="{tail}" fill="#000" stroke="#000" stroke-width="7" stroke-linejoin="round"/></mask>'
        f'<path fill-rule="evenodd" mask="url(#qk)" fill="{c["word"]}" d="'
        f"M{x + 12},40 H{x + 32} Q{x + 44},40 {x + 44},52 V68 Q{x + 44},80 {x + 32},80 H{x + 12} Q{x},80 {x},68 V52 Q{x},40 {x + 12},40 Z "
        f'M{x + 14},48 Q{x + 8},48 {x + 8},54 V66 Q{x + 8},72 {x + 14},72 H{x + 30} Q{x + 36},72 {x + 36},66 V54 Q{x + 36},48 {x + 30},48 Z"/>'
        f'<path d="{tail}" fill="{c["word"]}"/>'
    )


def wordmark(c, x0):
    parts, x = [], x0
    for ch in "MAX":
        parts.append(f'<path transform="translate({x} 0)" d="{GLYPHS[ch]}" fill="{c["word"]}"/>')
        x += 54
    x += 16  # word space before Q
    parts.append(q_glyph(x, c))
    return "".join(parts), x + 50


def svg(w, h, motion, body, label):
    css = MOTION_CSS[motion]
    style = f"<style>{css}{REDUCED}</style>" if css else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{label}">{style}{body}</svg>\n'
    )


for theme, c in THEMES.items():
    for motion in MOTION_CSS:
        stem = "" if motion == "static" else "-animated"
        (OUT / f"mark{stem}-{theme}.svg").write_text(
            svg(120, 120, motion, mark_body(c), "Max Q"), encoding="utf-8", newline="\n")
        word, end = wordmark(c, 150)
        body = mark_body(c) + word
        (OUT / f"lockup{stem}-{theme}.svg").write_text(
            svg(end + 4, 120, motion, body, "Max Q"), encoding="utf-8", newline="\n")
print(sorted(p.name for p in OUT.glob("*.svg")))
