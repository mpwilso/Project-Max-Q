# Story bank index (John Doe, fictional)

One file per story. Each file opens with an HTML comment:

- `id`: the file's name, used in prep packs and the question bank (`story_used`).
- `traces`: claim ids from [../claims.json](../claims.json), `;`-separated. Every figure the story says
  must be in one of them; `claimcheck.py --spoken` fails a figure no claim states.
- `sources`: free text, where a reader can check the prose.
- `tier`: SPOKEN or INTERVIEW, the most restricted voice of any claim the story uses. An INTERVIEW story
  is told when asked and never volunteered in an intro.
- `competencies`, `jd_themes`: what it answers, for mapping a job description to stories.
- `hazards`: what the story must never drift into.

After the comment come `## Short` (about 20 seconds) and `## Full (STARR)`: Situation, Task, Action,
Result, and a Reflection (what he learned or would do the same way). A new story, or a new detail that
comes out in a mock, is verified and added to the claims before any answer uses it.

Check the whole bank with `python claimcheck.py --spoken examples/john_doe/stories/` (this file is skipped).

| Story | Tier | Competencies | Answers questions like |
|---|---|---|---|
| [intro](intro.md) | SPOKEN | tell me about yourself | "Tell me about yourself." Every prep starts from this; only the closing line and the order of one paragraph change. |
| [coupa-rollout](coupa-rollout.md) | SPOKEN | delivery, change management | "Tell me about a rollout you led." "How do you get people onto a new system?" |
| [close-program](close-program.md) | SPOKEN | process improvement, outcomes | "Tell me about improving a process." "What was the impact?" |
| [saying-no-at-intake](saying-no-at-intake.md) | SPOKEN | prioritization, saying no | "How do you prioritize competing requests?" "Tell me about saying no to a stakeholder." |
| [cutover-rollback](cutover-rollback.md) | INTERVIEW | ownership, learning from a setback | "Tell me about a failure." "A launch that went wrong?" (only when asked) |
| [landmines](landmines.md) | SPOKEN | honesty about gaps | SAP, managing people, certifications, Python, a years bar, relocation |
