# Labeled set 2: rules written before the run

The second fit-scorer set, `labeled-2.jsonl`: 24 made-up postings from invented companies, in the same shape as the first set and with the same label mix (6 apply, 6 maybe, 12 skip). Every company, title and text is invented. The traps are the same kinds as in set 1 with different specifics: the right title on the wrong job (t13), the right job under an odd title (t04), a good job in a place he cannot work (t15, t22, t24), a level jump (t09, t14), a contract role (t20), a sales role in disguise (t17), and a generic title hiding a different job (t21).

Who labeled it: Claude Fable 5.1, a different model from the scorer (Claude Opus 5.5), under the rules below, before any scoring run, on 2026-11-05. These are not a person's labels. The scorer never saw the labels or this file: it gets the posting, the profile and the rubric, nothing else.

## The rules, applied in order

Taken from [the profile](../../examples/john_doe/profile.md) and [the rubric](../../examples/john_doe/rubric.md).

1. **skip** if the role's core is one the profile rules out: managing people, sales or pre-sales with a quota, travel-heavy or billable consulting, or a contract or temporary role.
2. **skip** if the location does not work: onsite or hybrid anywhere but the Denver metro, or any role outside the United States. Remote in the US works.
3. **apply** if the daily work is product, program or product-owner work on finance or business systems (ERP, procurement, order-to-cash, procure-to-pay, month-end close), as an individual contributor, with required years at most 6, and rules 1, 2 and 4 do not fire.
4. **skip** if a required skill is one the profile says is not claimable (SAP, Oracle, machine learning, a certification), or the role's core is engineering the profile does not show, or the role is a different job behind a matching title.
5. **maybe** for everything else: an adjacent domain (data, HR systems, inventory, internal operations), a step down in scope (an analyst role), required years of 7 or more on work that otherwise fits, or operations work with no systems ownership.

One line of rationale per posting is the `why` field in `labeled-2.jsonl`, naming the rule that decided it.

## Running it

```
python evals/run_fit_eval.py --data evals/fit/labeled-2.jsonl --out evals/results/fit2_baseline.md
python evals/run_fit_eval.py --data evals/fit/labeled-2.jsonl --scorer claude-code --record --out evals/results/fit2_claude-code.md
```

The scorer runs once. Its answers are recorded under [evals/recorded/](../recorded/) so the report can be replayed without a model call.

## Result (2026-11-05)

Scored once with Claude Opus 5.5 through Claude Code 2.1.318 on Windows, with `ANTHROPIC_MODEL` pinned to the model set 1 used. A first attempt from the WSL machine failed on every posting before any model call, because that Claude Code login had expired; it spent nothing and recorded nothing, and the run was made once from the other machine. Equivalent API cost about $0.55.

| Measure | Set 1 | Set 2 |
|---|---|---|
| Agrees with the label | 79% (19 of 24) | 79% (19 of 24) |
| Precision on apply | 67% | 55% |
| Recall on apply | 100% | 100% |
| Good jobs buried | 0 | 0 |
| Bad jobs pushed up | 0 | 0 |

Every miss on set 2 is a maybe the scorer called apply (t08 HR systems, t09 a 9+ years ask, t10 an analyst role, t11 inventory planning, t12 operations with no systems ownership), with scores of 76 to 81, just over the apply line. The dealbreaker rule, chosen after set 1, held on every skip in set 2, including the three location traps. Full report: [fit2_claude-code.md](../results/fit2_claude-code.md).
