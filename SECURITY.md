# Security

## Reporting a vulnerability

Please report it privately through GitHub: on this repository's **Security** tab, choose **Report a vulnerability**. Don't open a public issue. Max Q is a portfolio project maintained by one person, so there is no bounty.

## In scope

Max Q reads job postings that careers sites and their vendors wrote, and it holds a candidate's facts, so these count:

- A posting or board response that makes the sweep read or write a file outside `data/`, `reports/` or a path you passed on the command line.
- Text from a posting that reaches the terminal as control characters or escape sequences, or reaches a report as HTML, a script or a link other than the posting's own.
- Posting text that changes another posting's score, gets past the code checks on the scorer's answer, or gives the `claude-code` scorer a tool, a file or a saved session.
- Anything that sends the candidate's profile, claims or resume to a careers site, or to anyone other than the Claude scorer you chose.
- A malformed response that crashes, hangs or exhausts memory instead of being reported as an unread board.
- Anything in `.github/workflows/` that could expose a token or run untrusted code with write access.

## Out of scope

- The careers sites themselves, and a site that blocks or rate-limits the sweep.
- What a site's terms say about `--allow-browser-impersonation`. It is off by default; turning it on is the user's choice.
- Whether a filter, a score or a resume check is right. That's a design question; open an issue.
- The sibling projects named in the README. Each has its own repository.
