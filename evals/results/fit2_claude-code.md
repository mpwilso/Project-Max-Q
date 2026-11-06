# Fit scorer evaluation: claude-code

Model: `claude-opus-5-5`.

Data: `evals/fit/labeled-2.jsonl` (24 labeled postings). Run 2026-11-05.

| Measure | Result |
|---|---|
| Agrees with the label | 79% (19 of 24) |
| Precision on apply (scored apply and labeled apply) | 55% |
| Recall on apply (labeled apply and scored apply) | 100% |
| Good jobs buried (labeled apply, scored skip) | 0 |
| Bad jobs pushed up (labeled skip, scored apply) | 0 |
| Could not score | 0 |

Rows are the person's label, columns the scorer's verdict.

| label \ scored | apply | maybe | skip |
|---|---|---|---|
| apply | 6 | 0 | 0 |
| maybe | 5 | 1 | 0 |
| skip | 0 | 0 | 12 |

Run through Claude Code on a subscription; the same calls at API prices would cost about $0.55.

## Disagreements (5)

- **t08** Product Owner, HR Systems (Juniper Learning): labeled **maybe**, scored **apply** (80). Label reason: Systems product ownership, but HR systems are adjacent to his finance lane (rule 5).
- **t09** Senior Program Manager, Enterprise Applications (Copperfield Medical): labeled **maybe**, scored **apply** (81). Label reason: Strong match on the work, but 9+ years is past the reach rule 3 allows (rule 5).
- **t10** Systems Analyst, Finance Applications (Kestrel Aerospace): labeled **maybe**, scored **apply** (78). Label reason: His systems and city, but an analyst role is a step below product ownership (rule 5).
- **t11** Product Manager, Inventory and Planning Systems (Sable Outdoor Co.): labeled **maybe**, scored **apply** (77). Label reason: Product work on business systems, but inventory planning is an adjacent domain (rule 5).
- **t12** Program Manager, Vendor Onboarding Operations (Bluefin Payments): labeled **maybe**, scored **apply** (76). Label reason: Procurement-adjacent operations work with no systems ownership (rule 5).
