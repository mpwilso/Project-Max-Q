"""README rules. Offline: reads README.md and checks the files it links to exist.

Ported from the generic part of Loupe's tests/readme.test.ts. The rules, on README.md:
  1. shown lines (every <details> block collapsed to one line) under 155
  2. no em dash or en dash
  3. every relative link and image path names a file that exists; every #anchor matches a heading
  4. no bare angle-bracket placeholder in prose, outside code, beyond an allowlist of real tags
  5. the words only, first and best do not appear in prose (code, HTML comments, quoted lines and alt
     text excluded)
This file is ASCII only; the dashes are built with chr()."""
import re, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)
TAGS = {"p", "picture", "source", "img", "b", "details", "summary", "br", "a", "sub", "sup"}
MAX_SHOWN_LINES = 155


def prose(text):
    """The README with code blocks, code spans, HTML comments and quoted lines taken out: what GitHub
    shows as the README's own words and tags."""
    text = re.sub(r"^```.*?^```$", "", text, flags=re.S | re.M)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"^>.*$", "", text, flags=re.M)


def slug(heading):
    """GitHub's heading anchor: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r" ", "-", re.sub(r"[^\w\- ]", "", heading.strip().lower()))


def headings(text):
    return [re.sub(r"^#+ ", "", l) for l in prose(text).split("\n") if re.match(r"^#{1,6} ", l)]


def problems(text, base=ROOT):
    found = []
    shown = re.sub(r"<details>.*?</details>", "<details></details>", text, flags=re.S)
    lines = shown.count("\n") + (0 if shown.endswith("\n") or not shown else 1)
    if lines >= MAX_SHOWN_LINES:
        found.append(f"{lines} lines; keep it under {MAX_SHOWN_LINES}")
    if EM_DASH in text:
        found.append("an em dash")
    if EN_DASH in text:
        found.append("an en dash")
    anchors = [slug(h) for h in headings(text)]
    for m in re.finditer(r"\]\(([^)\s]+)\)|\b(?:src|srcset|href)=\"([^\"]+)\"", text):
        link = m.group(1) or m.group(2)
        if link.startswith("#"):
            if link[1:] not in anchors:
                found.append(f"an anchor with no heading: {link}")
            continue
        if re.match(r"^(https?:|mailto:)", link):
            continue
        if not (base / re.split(r"[?#]", link)[0]).exists():
            found.append(f"a broken link: {link}")
    words = prose(text)
    for m in re.finditer(r"<(/?)([^\s>/]*)[^>]*>", words):
        if m.group(2).lower() not in TAGS:
            found.append(f"a bare angle-bracket placeholder: {m.group(0)}")
    words = re.sub(r"<[^>]*>", "", words)
    for m in re.finditer(r"\b(only|first|best)\b", words, flags=re.I):
        found.append(f'"{m.group(1)}" in the prose')
    return found


class ReadmeRules(unittest.TestCase):
    def test_the_readme_passes_every_rule(self):
        self.assertEqual(problems(README.read_text(encoding="utf-8")), [])

    def test_each_rule_catches_what_it_should(self):
        self.assertEqual(problems("line\n" * 155), ["155 lines; keep it under 155"])
        self.assertEqual(problems("<details>\n" + "line\n" * 155 + "</details>\n"), [])
        self.assertEqual(problems("## Setup\n\nJump to [setup](#setup) or [the end](#the-end).\n\n```\n## The end\n```\n"),
                         ["an anchor with no heading: #the-end"])
        self.assertEqual(problems("A long pause" + EM_DASH + "then more.\n"), ["an em dash"])
        self.assertEqual(problems("Pages 3" + EN_DASH + "5.\n"), ["an en dash"])
        self.assertEqual(problems('See [the notes](docs/nowhere.md) and <img src="brand/missing.svg">.\n'),
                         ["a broken link: docs/nowhere.md", "a broken link: brand/missing.svg"])
        self.assertEqual(problems("Show the draft under <the reason>.\n"),
                         ["a bare angle-bracket placeholder: <the reason>"])
        self.assertEqual(problems("Show the draft under `<the reason>`.\n\n```\n<folder>\n```\n"), [])
        self.assertEqual(problems('The first tool, and the best.\n<img src="LICENSE" alt="Our only mark">\n'),
                         ['"first" in the prose', '"best" in the prose'])
        self.assertEqual(problems("```markdown\nFirst question: only the next box?\n```\n"), [])
        self.assertEqual(problems("> Only a quote says first.\n<!-- the best comment -->\n"), [])


if __name__ == "__main__":
    unittest.main()
