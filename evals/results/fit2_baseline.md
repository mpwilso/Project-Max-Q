# Fit scorer evaluation: baseline

Data: `evals/fit/labeled-2.jsonl` (24 labeled postings). Run 2026-11-05.

| Measure | Result |
|---|---|
| Agrees with the label | 50% (12 of 24) |
| Precision on apply (scored apply and labeled apply) | 46% |
| Recall on apply (labeled apply and scored apply) | 100% |
| Good jobs buried (labeled apply, scored skip) | 0 |
| Bad jobs pushed up (labeled skip, scored apply) | 4 |
| Could not score | 0 |

Rows are the person's label, columns the scorer's verdict.

| label \ scored | apply | maybe | skip |
|---|---|---|---|
| apply | 6 | 0 | 0 |
| maybe | 3 | 1 | 2 |
| skip | 4 | 3 | 5 |

## Disagreements (12)

- **t07** Product Manager, Analytics Platform (Tessera Robotics): labeled **maybe**, scored **apply** (83). Label reason: Product work with SQL, but a data platform is an adjacent domain, not finance systems (rule 5).
- **t08** Product Owner, HR Systems (Juniper Learning): labeled **maybe**, scored **apply** (92). Label reason: Systems product ownership, but HR systems are adjacent to his finance lane (rule 5).
- **t09** Senior Program Manager, Enterprise Applications (Copperfield Medical): labeled **maybe**, scored **skip** (20). Label reason: Strong match on the work, but 9+ years is past the reach rule 3 allows (rule 5).
- **t11** Product Manager, Inventory and Planning Systems (Sable Outdoor Co.): labeled **maybe**, scored **apply** (86). Label reason: Product work on business systems, but inventory planning is an adjacent domain (rule 5).
- **t12** Program Manager, Vendor Onboarding Operations (Bluefin Payments): labeled **maybe**, scored **skip** (50). Label reason: Procurement-adjacent operations work with no systems ownership (rule 5).
- **t13** Product Manager, Developer Experience (Pinecrest Software): labeled **skip**, scored **maybe** (68). Label reason: Title matches, but developer tooling is a different job behind it (rule 4).
- **t15** Product Owner, NetSuite (Meridian Foods): labeled **skip**, scored **apply** (100). Label reason: Right job, but onsite in Atlanta and he will not relocate (rule 2).
- **t16** Oracle Fusion Cloud ERP Product Manager (Oakline Energy): labeled **skip**, scored **apply** (100). Label reason: Requires Oracle ERP experience and a certification, neither claimable (rule 4).
- **t19** Product Manager, Perception ML (Tessera Robotics): labeled **skip**, scored **maybe** (74). Label reason: Machine learning core is not claimable (rule 4).
- **t20** Procurement Systems Product Owner (12-month contract) (Juniper Learning): labeled **skip**, scored **apply** (89). Label reason: Contract role; he wants a permanent one (rule 1).
- **t23** Technical Product Manager, Ecommerce Checkout (Sable Outdoor Co.): labeled **skip**, scored **maybe** (68). Label reason: Transaction-systems engineering core the profile does not show (rule 4).
- **t24** Product Manager, Treasury and Cash Management (Bluefin Payments): labeled **skip**, scored **apply** (80). Label reason: His domain, but hybrid in Chicago and he will not relocate (rule 2).
