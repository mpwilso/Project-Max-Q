# Fit scorer evaluation: claude-code (replayed)

Model: `claude-opus-5-5`.

Data: `evals/fit/labeled.jsonl` (24 labeled postings). Run 2026-09-28.

| Measure | Result |
|---|---|
| Agrees with the label | 79% (19 of 24) |
| Precision on apply (scored apply and labeled apply) | 67% |
| Recall on apply (labeled apply and scored apply) | 100% |
| Good jobs buried (labeled apply, scored skip) | 0 |
| Bad jobs pushed up (labeled skip, scored apply) | 0 |
| Could not score | 0 |

Rows are the person's label, columns the scorer's verdict.

| label \ scored | apply | maybe | skip |
|---|---|---|---|
| apply | 6 | 0 | 0 |
| maybe | 3 | 2 | 1 |
| skip | 0 | 1 | 11 |

## Disagreements (5)

- **s12** Program Manager, Revenue Operations (Initech): labeled **skip**, scored **maybe** (57). Label reason: Sales-facing revenue systems, closer to a sales role than his lane.
- **s13** Business Systems Analyst, Finance (Contoso Health): labeled **maybe**, scored **apply** (81). Label reason: His systems and city, but an analyst role is a step below his scope.
- **s20** Product Manager, Internal Tools (Globex Systems): labeled **maybe**, scored **skip** (51). Label reason: Product work he could do, but Chicago onsite is not one of his markets.
- **s22** Senior Program Manager, Business Systems (Contoso Health): labeled **maybe**, scored **apply** (81). Label reason: Strong match on work, but 8+ years is a reach from 4.5.
- **s23** Product Owner, Warehouse Systems (Fabrikam Freight): labeled **maybe**, scored **apply** (78). Label reason: Product-owner work in Denver, but warehouse systems are a different domain.
