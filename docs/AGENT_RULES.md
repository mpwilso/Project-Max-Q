# Operating rules for the coding agent

maxq was built by an AI coding agent (Claude Code) working under written operating instructions. These
are the rules from those instructions that shaped this repository, generalized. They are the point of
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
