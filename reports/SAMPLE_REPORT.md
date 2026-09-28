<!-- Trimmed from a real run of the example profile in gates.json (John Doe, fictional): 7 employers. Sections 1 and 2 keep a few entries. -->

# Sweep report, 2026-09-27

Discovery by script against first-party ATS JSON endpoints. **Only one snapshot exists, so first_seen is NOT yet a signal** (run one stamped every key to the same date). Freshness below falls back to board posted dates. Window 14 days; evergreen after 180 days.

## Sweep health

- Funnel: 2364 reqs (0 carried forward) from 7 live employer(s) -> 1107 title excluded, 1066 title out of lane, 95 location gate (fail-closed, no US marker), 1 engineering title without product/program work -> 51 LOCATION-POLICY, 5 TENURE, 0 UNCOVERED, 17 REVIEW (body-rescued) -> **22 PASS** (22 new; previous snapshot 0)
- No anomalies: every employer read, no count drops, pass volume in line.
- etag: 0 of 0 conditional reads would have been 304 (0 with a body that differed from the snapshot); 7 of 7 ETag boards returned a tag to store (measurement mode, gates.json etag_trust off); clean streak 0 of 3 full sweeps

## 0. Changes to watch

Reqs whose posting text changed since the last read are listed here.

## 1. New or newly eligible, gate-passing

### [Databricks · Senior Customer Enablement Program Manager](https://databricks.com/company/careers/open-positions/job?gh_jid=8535311002)
- Location: United States
- Dates: posted 2026-05-12 [PRIMARY] · then first seen 2026-09-27 (run-one stamp, not a signal) · 138d old
- Posted (board): 2026-05-12
- Comp: $117,400 — $161,350
- ATS id: `greenhouse:Databricks:8535311002`
- Conversion read: **LOW** · years gap +2 (6+ stated vs 4.5 held) · posted 138d ago (past 14 days: a drag) · flooded board (hundreds of applicants per posting) · no known contact
- Why now: newly discovered
- Posting risk: live 138d
- Flags: VERIFY LOCATION: the board names only the country (United States); confirm remote or an endorsed market before building; REACH: 6+ years stated
- JD text: `data\jds\2026-09-27\greenhouse_databricks_8535311002.md`

### [Datadog · Principal Partner Program Manager - Datadog Partner Network Solution Provider Program](https://careers.datadoghq.com/detail/7984983/?gh_jid=7984983)
- Location: Boston, Massachusetts, USA; Denver, Colorado, USA; New York, New York, USA; San Francisco, California, USA
- Dates: posted 2026-06-22 [PRIMARY] · then first seen 2026-09-27 (run-one stamp, not a signal) · 97d old
- Posted (board): 2026-06-22
- Comp: $197,000 — $246,000
- ATS id: `greenhouse:Datadog:7984983`
- Conversion read: **LOW** · years gap +4 (8+ stated vs 4.5 held) · level-up title (principal) · posted 97d ago (past 14 days: a drag) · no known contact
- Why now: newly discovered
- Posting risk: live 97d; generic requirements, no named tools
- Flags: REACH: 8+ years stated
- JD text: `data\jds\2026-09-27\greenhouse_datadog_7984983.md`

### [Notion · Product Operations Manager](https://jobs.ashbyhq.com/notion/8b82e596-e828-45db-94d5-b76acc89e749)
- Location: San Francisco, California | New York, New York | Remote
- Dates: posted 2026-02-12 [PRIMARY] · then first seen 2026-09-27 (run-one stamp, not a signal) · 227d old
- Posted (board): 2026-02-12
- Comp: $160,000-$200,000
- ATS id: `ashby:Notion:8b82e596-e828-45db-94d5-b76acc89e749`
- Conversion read: **MEDIUM** · years bar 4+ (stated) cleared · posted 227d ago (past 14 days: a drag) · no known contact
- Why now: newly discovered
- Posting risk: live 227d
- Flags: ONSITE TERMS despite remote listing: expect every notino to be intellectually curious, drawn to tinkering and discovery, and
- JD text: `data\jds\2026-09-27\ashby_notion_8b82e596_e828_45db_94d5_b76acc89e749.md`

_... 19 more ..._

## 2. New or newly flagged (for a person to review)

- **REVIEW** [Stripe · Business Partner Analyst](https://stripe.com/jobs/search?gh_jid=8079783) · US-SF, US-Seattle, US-NYC, US-Chicago, US-Georgia or US-Remote · RESCUED on body score 8 (floor 7)
- **REVIEW** [Stripe · Communities Partner Development Manager, SaaS Platforms](https://stripe.com/jobs/search?gh_jid=8103952) · US-Remote · RESCUED on body score 9 (floor 7); REACH: 8+ years stated
- **REVIEW** [Stripe · Communities Partner Development Manager, SaaS Platforms](https://stripe.com/jobs/search?gh_jid=8138000) · US-Remote · RESCUED on body score 9 (floor 7); REACH: 8+ years stated
- **REVIEW** [Stripe · Deal Strategist](https://stripe.com/jobs/search?gh_jid=7958150) · US-Remote · RESCUED on body score 11 (floor 7); REACH: 6+ years stated
- _... more ..._

## 3. Closed since last run (previously gate-passing)

None.

## 4. Still open, gate-passing (aging)

None.

## 5. Coverage

| Company | ATS | Reqs read | Status |
|---|---|---|---|
| [Stripe](https://stripe.com) | greenhouse | 699 | OK |
| [Databricks](https://databricks.com) | greenhouse | 886 | OK |
| [Datadog](https://careers.datadoghq.com) | greenhouse | 448 | OK |
| [Linear](https://jobs.ashbyhq.com) | ashby | 30 | OK |
| [Figma](https://boards.greenhouse.io) | greenhouse | 163 | OK |
| [Notion](https://jobs.ashbyhq.com) | ashby | 128 | OK |
| [Zapier](https://jobs.ashbyhq.com) | ashby | 10 | OK |

New reqs gated out this run: 2269 title out of lane 1066, title excluded 1107, location gate (fail-closed, no US marker) 95, engineering title without product/program work 1
