# Operating rules for the coding agent

Claude Code wrote most of maxq's code under operating rules I wrote and enforced. These are the rules
from those instructions that shaped this repository, generalized. They are the point of
the project as much as the code is: an agent that is fast is only useful if its work can be trusted.

## What the agent may and may not decide
- The agent reads boards, applies the gates, sorts the unscored pile and drafts assessments.
- A person decides which roles are worth an application, and a person submits anything, always.
- The agent never logs in anywhere and never acts on a posting by itself.

## Evidence before claims
- A small result is a suspected defect until the funnel says otherwise. Report the funnel, every
  uncovered employer and every warning; never summarize a run as "quiet" without them.
- Coverage is reported, not assumed: an employer that errored is UNCOVERED, never quiet.
- Only a complete, clean read of a board may close a posting. Anything else is carried forward.
- An optimization is measured before it is trusted (conditional requests run in measurement mode until
  three clean full sweeps agree with them).

## Every defect becomes a test
- A defect found in real data gets a regression test in the same session.
- Run the full offline suite before a long sweep and after editing the sweep, the gates or an adapter.
- A rule the agent keeps breaking becomes a check. Rewriting a whole file to change one line happened
  again after it was written down, so a pre-commit hook now refuses it (`tools/guard_rewrite.py`).

## Changes to configuration have a blast radius
- After any edit to `gates.json`, rebuild the report from the last snapshot (`--report-only`) and read
  what became eligible. The edit is not done until its effect has been looked at.
- A lexicon or ranking change is re-validated against scored history before it is relied on.

## Extending the system
- A new board type is a plugin in `adapters/`, written to the contract in `adapters/README.md`,
  probed against the live board, and covered by a fixture-backed test before any employer uses it.
- Aggregator sites are not sources. Only an employer's own board is read.

## Honesty in what gets written
- Output states what the code measured and what it did not. A sort order is never presented as a
  verdict, and a heuristic is labelled as one.

## Interview prep, mock interviews and debriefs
- Every spoken line traces to a verified claim and agrees with the resume the interviewer holds. A claim
  marked spoken is said, never printed; one marked interview waits to be asked; an estimate keeps its word.
  `claimcheck.py --spoken` passes before a pack reaches the candidate, and every warning is fixed or
  explained.
- The intro starts from the one the candidate approved. Only the closing line changes per role; anything
  else is shown to the candidate as a before and after, and goes into the canonical intro on a yes.
- A sheet for the next interviewer is built from the pack, never edited from the last interviewer's sheet.
- The check sees figures and phrases, not invented habits. An independent read of the pack against the
  claims, the lines with no number first, happens before the candidate sees it.
- A detail that comes out in a mock and is not in the claims is asked about, yes or no, and not reused
  until it is verified. Mock transcripts carry the pack's header so their answers are held to its claims.
- A booking and a round held are recorded as separate events. Interviewers are not referrals.
- Thank-you notes are replies, short, in the candidate's voice; his own draft is polished, not replaced.
- No help during an interview, ever. The pack is for before.
