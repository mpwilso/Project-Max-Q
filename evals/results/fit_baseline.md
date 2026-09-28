# Fit scorer evaluation: baseline

Data: `evals/fit/labeled.jsonl` (24 labeled postings). Run 2026-09-28.

| Measure | Result |
|---|---|
| Agrees with the label | 46% (11 of 24) |
| Precision on apply (scored apply and labeled apply) | 42% |
| Recall on apply (labeled apply and scored apply) | 83% |
| Good jobs buried (labeled apply, scored skip) | 0 |
| Bad jobs pushed up (labeled skip, scored apply) | 3 |
| Could not score | 0 |

Rows are the person's label, columns the scorer's verdict.

| label \ scored | apply | maybe | skip |
|---|---|---|---|
| apply | 5 | 1 | 0 |
| maybe | 4 | 1 | 1 |
| skip | 3 | 4 | 5 |

## Disagreements (13)

- **s05** Product Manager, Growth (Contoso Health): labeled **skip**, scored **maybe** (68). Label reason: Title matches, but consumer growth experimentation is not his work.
- **s08** SAP S/4HANA Product Owner (Wide World Importers): labeled **skip**, scored **apply** (77). Label reason: Requires SAP and a certification he does not have.
- **s11** Product Operations Manager (Globex Systems): labeled **maybe**, scored **apply** (77). Label reason: Adjacent operations work; no systems ownership.
- **s13** Business Systems Analyst, Finance (Contoso Health): labeled **maybe**, scored **apply** (86). Label reason: His systems and city, but an analyst role is a step below his scope.
- **s14** Product Manager, Procure-to-Pay (Fabrikam Freight): labeled **skip**, scored **apply** (98). Label reason: Good fit on paper, but onsite in Canada and he will not relocate.
- **s15** Senior Technical Product Manager, Payments Infrastructure (Tailspin Toys): labeled **skip**, scored **maybe** (59). Label reason: Deep distributed-systems core he does not have.
- **s16** Order Management Systems Lead (Wide World Importers): labeled **apply**, scored **maybe** (74). Label reason: Unusual title, but order-to-cash in NetSuite is his core.
- **s17** Product Manager, AI Platform (Litware): labeled **skip**, scored **maybe** (65). Label reason: Machine learning core; not his domain.
- **s18** ERP Product Manager (Contract, 6 months) (Northwind Traders): labeled **skip**, scored **apply** (92). Label reason: Contract role; he wants a permanent one.
- **s20** Product Manager, Internal Tools (Globex Systems): labeled **maybe**, scored **apply** (86). Label reason: Product work he could do, but Chicago onsite is not one of his markets.
- **s22** Senior Program Manager, Business Systems (Contoso Health): labeled **maybe**, scored **apply** (86). Label reason: Strong match on work, but 8+ years is a reach from 4.5.
- **s23** Product Owner, Warehouse Systems (Fabrikam Freight): labeled **maybe**, scored **skip** (20). Label reason: Product-owner work in Denver, but warehouse systems are a different domain.
- **s24** Technical Program Manager (Tailspin Toys): labeled **skip**, scored **maybe** (68). Label reason: Games live-service production; generic title hides a different job.
