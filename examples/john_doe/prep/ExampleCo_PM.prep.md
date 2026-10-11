<!--
key: exampleco-pm-finance-systems (fictional company and posting)
link: https://example.com/careers/finance-systems-pm
stage: hm (hiring manager, 45 minutes)
date: Tue Nov 5, 2030, 10:00 MT, video
people: Casey Nakamura (hiring manager; from the invite; sheet in ExampleCo_PM_Casey.prep.md); Priya Raman (recruiter; screened him Oct 29, 2030)
level: the posting's level, from the recruiter; the band is the posting's
resume: ../resume.md
traces: tenure; erp-owner; backlog; coupa-rollout; rollout-approach; close-program; close-approach; releases; reporting; intake-rubric
-->
# ExampleCo · Product Manager, Finance Systems · hiring manager round

The pack for the whole process. Each `## ` heading is a question and the text under it is the answer, said
aloud; a `### ` heading starts notes that are not checked. Every figure traces to a claim the file's
`traces:` line names, or to one an answer cites. Check it with
`python claimcheck.py --spoken examples/john_doe/prep/ExampleCo_PM.prep.md`.

Signals from the screen (`python tools/question_bank.py --company ExampleCo --bank examples/john_doe/interviews/questions.jsonl`):
the team is replacing spreadsheet steps in its close and wants someone who has shortened one. Lead with
the close program, then the rollout.

## Tell me about yourself, or walk me through your background
<!-- story: intro. Closing line written for this team, from the screen's signal. -->
I'm the product owner for finance systems at Northwind Traders, a distributor, and I've been there 4.5 years. I own our NetSuite ERP across order-to-cash and procure-to-pay, and I chair the weekly intake meeting where the finance systems team decides what gets built next.

Two pieces of work I'd point to. I ran the month-end close program, which took the close from 9 working days to 6. And I led the Coupa procurement rollout to 3 business units and 420 requesters, one business unit at a time, with requester training before each go-live.

Day to day, I prioritize the backlog, I coordinate user acceptance testing and release sign-off for our quarterly ERP releases, and I build the SQL and Excel reporting finance leadership uses.

You're taking spreadsheet steps out of your close, and that's the work I'd most like to do again.

## Why this role?
The posting asks for someone who has owned an ERP end to end and shortened a close. Those are the two things I've spent the last 4.5 years on, and this role would let me keep doing both.

## What did the close program change?
<!-- story: close-program. The 120 hours always carries "estimate" in the same sentence. -->
<!-- cites: close-hours -->
The close went from 9 working days to 6. I mapped every close task with the accounting team first, then moved the bank reconciliations and accrual entries into NetSuite saved searches, and a daily close stand-up replaced the end-of-month status email. My estimate from the accounting team's timesheets is that it freed 120 analyst hours a month.

## Tell me about a rollout you led.
<!-- story: coupa-rollout -->
I led our Coupa procurement rollout to 3 business units and 420 requesters. I chose to go one business unit at a time, ran requester training before each go-live, and owned the cutover checklist every unit went through. All 3 units and all 420 requesters moved onto Coupa.

## Tell me about a time something you launched went wrong.
<!-- story: cutover-rollback (interview-only: this is the question that earns it). -->
<!-- cites: cutover-rollback -->
The Coupa cutover for our third business unit was rolled back after 2 days. Its supplier records had never been cleaned, and my checklist had no step for supplier data, so that was my miss. I ran the cleanup of 1,800 duplicate supplier records, added a supplier-data check to the checklist, and the unit went live 5 weeks later.

## How do you prioritize competing requests?
<!-- story: saying-no-at-intake -->
<!-- cites: intake-decline -->
Every request that comes to our weekly intake meeting is ranked on value, effort and risk, and the ranked list is shared with requesters, so a no comes with its reason. When a region asked for a custom Coupa approval workflow, I showed them the standard approval flow already covered their case, and they adopted the standard flow.

## Have you worked in SAP?
<!-- landmines. The posting says "NetSuite or SAP". -->
I haven't worked in SAP. My ERP is NetSuite, across order-to-cash and procure-to-pay. The processes carry over; the configuration model I'd have to learn, and I'd start from your process maps.

## Do you have people-management experience?
<!-- landmines -->
No direct reports, and I've never hired anyone. What I do lead is the intake meeting: I chair it, I prioritize the backlog for the finance systems team, and the ranked list is what gets built.

## What are you looking for in pay?
<!-- untraced-ok: $118K to $135K -->
<!-- The band is the posting's, not a claim. Level first, then the number. -->
The posting lists $118K to $135K. I'm comfortable inside that range, and I'd like to understand the level before I name a number.

### Questions to ask Casey
- Which close steps are still in spreadsheets, and which one hurts most?
- Who owns NetSuite configuration today, and how does work reach them?
- What would make the first six months a success for this role?

### JD map
| Posting asks for | Story | Note |
|---|---|---|
| Owned an ERP end to end | intro, close-program | NetSuite, order-to-cash and procure-to-pay |
| Shortened a financial close | close-program | the estimate keeps its word |
| Led a system rollout | coupa-rollout | cutover-rollback only when asked |
| Prioritized a backlog with stakeholders | saying-no-at-intake | |
| NetSuite or SAP | landmines | GAP on SAP: answered straight |
