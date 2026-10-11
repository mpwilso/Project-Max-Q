# Interview prep

Once an application reaches a screen or an interview, the rule that governs the resume governs what gets
said aloud: every figure and every claim traces to a verified claim about the candidate, and nothing
contradicts the page the interviewer is holding. A spoken answer is easier to embellish than a bullet,
because nobody reviews it before it is said. This is the workflow, and the check that holds it to the
claims. The worked example is the fictional John Doe in [examples/john_doe/](../examples/john_doe/).

```
python claimcheck.py --spoken examples/john_doe/prep/ExampleCo_PM.prep.md   # a prep pack
python claimcheck.py --spoken examples/john_doe/stories/                    # the whole story bank
python claimcheck.py --spoken screen.prep.md --stage screen                 # a recruiter screen
python tools/question_bank.py --check examples/john_doe/prep/ExampleCo_PM.prep.md \
    --bank examples/john_doe/interviews/questions.jsonl                     # questions asked before
```

## 1. Claims have a voice

The claims file ([claims.json](../examples/john_doe/claims.json)) is the same one the resume check reads.
Three optional keys per claim say how it may be used:

| Key | Meaning |
|---|---|
| `voice` | `written` (the default): on the page and aloud. `spoken`: aloud only, never printed, such as an estimate the candidate made himself. `interview`: aloud, and only when asked, such as a setback. |
| `patterns` | Case-insensitive regexes that recognise the claim in prose. A `spoken` or `interview` claim needs them, and so does one with `requires`, because nothing else can tell that it is being said. |
| `requires` | A regex the same sentence must also match whenever the claim is said. John Doe's 120 analyst hours are his own estimate, so the sentence must say "estimate". |

Two lists sit beside the claims: `never_claim` (phrases barred in either voice, such as "hired" or "direct
reports" for a candidate who never managed anyone) and `titles_not_held` (titles he must never be
presented as holding, matched title-cased and case-sensitive, so "a product manager's view" passes and
"Product Manager" does not).

In JSON a word boundary is written `"\\b"`. A plain `"\b"` decodes to a backspace character, which compiles,
matches nothing and never fires; claimcheck refuses a claims file that contains one.

## 2. The story bank

One Markdown file per story ([stories/](../examples/john_doe/stories/), mapped by its
[INDEX.md](../examples/john_doe/stories/INDEX.md)). Each opens with an HTML comment:

```
<!--
id: close-program
traces: close-program; close-approach; close-hours
sources: where a reader can check the prose
tier: SPOKEN                  (or INTERVIEW: the most restricted voice of any claim it uses)
competencies: process improvement, measurable outcomes
jd_themes: record-to-report; month-end close
hazards: what the story must never drift into
-->
```

then `## Short` (about 20 seconds) and `## Full (STARR)`: Situation, Task, Action, Result, and a
Reflection. `landmines.md` holds the straight answers to known gaps (a system he has not used, people
management, a years bar above his): the plain truth first, then the nearest real work. A new story, or a
new detail that comes out in a mock, is verified and added to the claims before any answer reuses it.

**The canonical intro.** `stories/intro.md` is the one "tell me about yourself" the candidate approved.
Every prep starts from it, keeps its order and its figures, and changes only the closing line (written
fresh from the posting, or from what the interviewer said the team wants) and, for some rounds, the order
of one paragraph. Any other change is a proposal to the candidate, and on a yes it goes into `intro.md`,
so the intro converges instead of being rewritten for every round. An intro never volunteers
interview-only material: the check warns (`volunteered`).

## 3. A pack per role, a sheet per interviewer

The **prep pack** ([ExampleCo_PM.prep.md](../examples/john_doe/prep/ExampleCo_PM.prep.md)) covers the
whole process for one role. Its header comment carries the key, link, stage, date, people, level, and two
lines the check reads:

- `traces: <claim id>; <claim id>`: the claims every answer may draw figures from. One answer can add more
  with `<!-- cites: <claim id> -->`.
- `resume: ../resume.md`: the page the interviewer holds. `resume_coverage` lists its figures the pack
  never says; those are the likeliest cold questions, so answer them in the pack.

What goes in depends on the stage:

- **Screen** (a recruiter, 15 to 30 minutes): the intro, why this role, two or three short stories, the
  landmines, pay (the level first; the band from the posting), location, authorization, timeline, two
  questions to ask. Answers under 30 seconds except the intro; check with `--stage screen`.
- **Hiring manager**: the intro, then the full STARR form of the 4 to 6 stories that map to the posting's
  responsibilities, a point of view on the team's problem, and questions that show he read what they build.
- **Loop**: one story per competency the employer names, no story told twice in a day, a setback story,
  and questions for each interviewer's function. Put each interviewer's date beside their name; never
  assume a loop is one day.

Map every requirement and responsibility in the posting to a story, or to GAP. A GAP gets an honest
bridge in the landmines style; it never gets a story stretched over it. Questions an earlier round asked
(any employer) come from `tools/question_bank.py --check`; answer each in the pack or say in a comment why
it cannot come up.

The **interviewer sheet** ([ExampleCo_PM_Casey.prep.md](../examples/john_doe/prep/ExampleCo_PM_Casey.prep.md))
is one page for one person, built from the pack and never edited from the last person's sheet (a sheet
"refreshed" for the next interviewer still opens on the last one). Its first line is
`# <Name> · <day, date, time, zone> · <length>`. Then: `## Intro` (from `intro.md`, closing line for this
person), who they are in two or three lines with the evidence for it (anything guessed says
"unconfirmed"), three to five `## <question>` answers this person's function will probe, the stories for
the round, three questions to ask, and the landmines this person is most likely to hit.

## 4. The check

The file convention: one `## <question>` heading per spoken answer. Any other heading (`### Notes`,
`# Part 2`) ends the answer above it, and nothing is read until the next `## `, so notes, a requirement
map or questions to ask never count toward an answer's length or figures. HTML comments are coaching
notes and are not read, except `traces:`, `resume:`, `cites:`, and:

- `<!-- untraced-ok: $118K to $135K -->`: a figure that is not a claim (a posted band, a date). It is
  allowed only where that exact phrase is said, unit and symbol included: `$95K` allows "$95K", never
  "95+".
- `<!-- done: debriefed <date> -->`: the round is over. Failures still block, because a claims change can
  break a sheet that gets reused, but its warnings stop printing.

| Check | Level | What it catches |
|---|---|---|
| `people_management` | FAIL | managed, supervised, oversaw or hired + a team or reports; "reported to me", "my reports", "team of four", a pod led. A negated sentence ("nobody reports to me") passes. |
| `held_titles` | FAIL | a title-cased title from `titles_not_held`, or a level above the one held. Naming the posting ("the Product Manager role") passes. |
| `never_claims` | FAIL | the never-claim list. "I haven't worked in SAP" and "working toward a certification" are the truthful answers and pass. |
| `spoken_estimate` | FAIL | a claim with `requires` said without it in the same sentence |
| `figure_trace` | FAIL | a figure (digits, or spelled with a unit: "eleven weeks") no claim states; a citation to a claim that does not exist |
| `figure_cites` | WARN | a figure some claim states, but not one this answer cites |
| `volunteered` | WARN | an interview-only claim in an intro |
| `unsupported_system` | WARN | a known system no claim mentions, said as used |
| `ai_tells` | WARN | the AI-tell words, which nobody says out loud |
| `answer_length` | WARN | over two minutes at 150 words a minute (the intro: 100 seconds; at a screen, 30 seconds). Every answer's seconds are printed. |
| `i_not_we` | WARN | an answer of 40 or more words with more "we" than "I": say what you did |
| `resume_coverage` | INFO | figures on the interviewer's page that the pack never says |

Hedges and em-dashes are not checked aloud: nothing here is sent, and an estimate needs its word. Exit
code 0 when nothing fails, 1 on any FAIL, 2 on a missing file or a malformed claims file.

**Then an independent read.** The check sees figures and phrases. An invented habit with no number in it
("I review every reconciliation myself") passes all of them. Before a pack is used, a second reader (a
fresh agent with only the pack, the sheet and the claims file) flags every spoken line that does not trace,
the lines with no number first. Fix what it finds.

## 5. Mock interviews

One question at a time, in the persona of the stage (a recruiter screens for fit, level and logistics; a
hiring manager goes deep on the responsibilities; a loop interviewer owns one competency), with one
follow-up drawn from what the candidate actually said. Each answer is appended to a session transcript
outside the repo's checked folders (a deliberate miss in a mock would fail every later run) as
`## <question>`, and the transcript gets the pack's header comment. That matters: an answer with no
citations of its own is then held to the pack's claims, not the whole file, so a figure that matches some
unrelated claim by coincidence is flagged instead of passing quietly. Run the check on the transcript;
a FAIL is the first thing the candidate hears about that answer.

Score each answer 1 to 5 on structure (STARR, Reflection included), specificity (a traced figure or a
named artifact), ownership ("I", not "we"), length and fit to the posting, and give the one change that
would lift the lowest score, with the rewritten sentence. A detail the candidate says that is not in the
claims is asked about as a yes/no question and not reused until it is verified. Log one line per answer,
the answer included in the candidate's own words, so a later claims change can re-check what was said.

## 6. Debrief

After each round:

1. **Booking and round are separate events.** Record the booking when it is made ("booked: hiring
   manager, <name>, <date>") and the round with the date it was held ("held: ...; next: ..."). A booked
   round that never happened must not look like one that did.
2. **Question bank.** Append each question asked to the questions file, one JSON line each:
   `{"type": "question", "date", "company", "key", "stage", "interviewer_role", "question", "story_used", "landed": "yes|no|unsure"}`.
   Then every **signal**, what the interviewer said the team wants, worries about or values, which is often
   worth more than the questions: `{"type": "signal", "date", "company", "key", "stage", "interviewer_role", "signal", "use_next": "<stage>: stories to lead with"}`.
   `tools/question_bank.py --company <X>` reads them back for the next round there; the default file,
   `data/interviews/questions.jsonl`, is gitignored.
3. **Stories.** A story that did not land gets a note in its header comment: what was asked, what was
   missing. A new true detail goes through verification before anything reuses it.
4. **People.** An interviewer is recorded as an interviewer, not as a referral; someone becomes a contact
   only when they offer to help.
5. **Thank-you notes,** one per interviewer, written as a reply, never as cold outreach. If the candidate
   has a rough version, polish it and keep his words. Otherwise: four sentences at most and under 90 words,
   a casual greeting, thanks, the one specific thing the interviewer said and why it stuck, and a plain
   sign-off with a first name. No restated qualifications, no pitch, no "excited to", no "I look forward to
   hearing", no stock opener ("Thanks for the time today"), one thank-you only, nothing about fit. Count
   the words before offering it.
6. **Retire the sheet.** Add `<!-- done: debriefed <date> -->` as the first line of that interviewer's
   sheet, and of the pack when the process ends, so the warning list stays about rounds still ahead.

## Never during the interview

The pack is for before. No assistance during an interview, ever: many employers' candidate codes of
conduct bar outside help in the room, and it is dishonest everywhere.
