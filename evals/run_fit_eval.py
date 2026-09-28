"""run_fit_eval.py - measure a fit scorer against postings a person has labeled.

    python evals/run_fit_eval.py                         # baseline scorer on the synthetic set
    python evals/run_fit_eval.py --scorer claude --record  # Claude, and save its answers for replay
    python evals/run_fit_eval.py --scorer replay --replay evals/recorded/claude-<date>.json
    python evals/run_fit_eval.py --data evals/local/mine.jsonl  # your own labels, kept out of git

Each line of the data file is one posting with a person's label: apply, maybe or skip. The report
says how often the scorer agrees, where it disagrees, and which disagreements are expensive: a good
job it would bury (labeled apply, scored skip) and a bad one it would push to the top (labeled skip,
scored apply). A data file under evals/local/ writes its report there too, and that folder is
gitignored, so real postings never reach the public repository.
"""
import argparse, datetime as dt, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fitscore  # noqa: E402

DEFAULT_DATA = ROOT / "evals" / "fit" / "labeled.jsonl"
ORDER = fitscore.VERDICTS                                # apply, maybe, skip


def load(path):
    items = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = [i.get("id") for i in items if i.get("label") not in ORDER]
    if bad:
        raise SystemExit(f"{path}: label must be one of {ORDER} (ids {bad})")
    return items


def metrics(pairs):
    """pairs: (label, predicted) with predicted None for a posting the scorer could not score."""
    scored = [(l, p) for l, p in pairs if p is not None]
    confusion = {l: {p: 0 for p in ORDER} for l in ORDER}
    for l, p in scored:
        confusion[l][p] += 1
    tp = confusion["apply"]["apply"]
    pred_apply = sum(confusion[l]["apply"] for l in ORDER)
    true_apply = sum(confusion["apply"].values())
    n = len(scored)
    return {
        "n": len(pairs), "scored": n, "errors": len(pairs) - n,
        "agreed": sum(confusion[l][l] for l in ORDER),
        "agreement": sum(confusion[l][l] for l in ORDER) / n if n else 0.0,
        "apply_precision": tp / pred_apply if pred_apply else None,
        "apply_recall": tp / true_apply if true_apply else None,
        "buried": confusion["apply"]["skip"],          # a good job scored skip
        "pushed": confusion["skip"]["apply"],          # a bad job scored apply
        "confusion": confusion,
    }


def rel(path):
    """A path as the report shows it: relative to the repo, never an absolute local path."""
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.name


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def report(name, data_path, rows, m, usage=None):
    L = [f"# Fit scorer evaluation: {name}", "",
         f"Data: `{rel(data_path)}` ({m['n']} labeled postings). Run {dt.date.today().isoformat()}.", "",
         "| Measure | Result |", "|---|---|",
         f"| Agrees with the label | {pct(m['agreement'])} ({m['agreed']} of {m['scored']}) |",
         f"| Precision on apply (scored apply and labeled apply) | {pct(m['apply_precision'])} |",
         f"| Recall on apply (labeled apply and scored apply) | {pct(m['apply_recall'])} |",
         f"| Good jobs buried (labeled apply, scored skip) | {m['buried']} |",
         f"| Bad jobs pushed up (labeled skip, scored apply) | {m['pushed']} |",
         f"| Could not score | {m['errors']} |", "",
         "Rows are the person's label, columns the scorer's verdict.", "",
         "| label \\ scored | " + " | ".join(ORDER) + " |", "|---|" + "---|" * len(ORDER)]
    for l in ORDER:
        L.append(f"| {l} | " + " | ".join(str(m["confusion"][l][p]) for p in ORDER) + " |")
    if usage and any(usage.values()):
        L += ["", f"Tokens: {usage['input_tokens']:,} input ({usage['cache_read_input_tokens']:,} from cache), "
                  f"{usage['output_tokens']:,} output."]
    miss = [r for r in rows if r["pred"] != r["label"]]
    L += ["", f"## Disagreements ({len(miss)})", ""]
    if not miss:
        L.append("None.")
    for r in miss:
        got = r["pred"] or f"error: {r['error']}"
        score = "" if r["score"] is None else f" ({r['score']})"
        L.append(f"- **{r['id']}** {r['title']} ({r['company']}): labeled **{r['label']}**, scored "
                 f"**{got}**{score}. Label reason: {r['why']}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Measure a fit scorer against labeled postings.")
    ap.add_argument("--scorer", default="baseline", choices=["baseline", "claude", "replay"])
    ap.add_argument("--replay", help="recorded answers for --scorer replay")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--record", action="store_true", help="save the scorer's answers for later replay")
    ap.add_argument("--out", help="report path (default: evals/results/, or evals/local/ for local data)")
    a = ap.parse_args()

    items = load(a.data)
    scorer = fitscore.make_scorer(a.scorer, a.replay)
    rows, answers = [], {}
    for it in items:
        try:
            res = scorer.score(it)
            answers[it["id"]] = res
            rows.append({**it, "pred": res["verdict"], "score": res["score"], "error": None})
        except ValueError as e:
            rows.append({**it, "pred": None, "score": None, "error": str(e)})
        print(f"  {it['id']:>4}  label {it['label']:5}  scored {rows[-1]['pred'] or 'ERROR':5}  {it['title']}")

    m = metrics([(r["label"], r["pred"]) for r in rows])
    local = "evals/local" in Path(a.data).resolve().as_posix()
    out_dir = ROOT / "evals" / ("local" if local else "results")
    out = Path(a.out) if a.out else out_dir / f"fit_{scorer.name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(scorer.name, a.data, rows, m, getattr(scorer, "usage", None)), encoding="utf-8", newline="\n")
    if a.record:
        rec_dir = ROOT / "evals" / ("local" if local else "recorded")
        rec_dir.mkdir(parents=True, exist_ok=True)
        rec = rec_dir / f"{scorer.name}-{dt.date.today().isoformat()}.json"
        rec.write_text(json.dumps({"scorer": scorer.name, "model": getattr(scorer, "model", None),
                                   "data": rel(a.data), "answers": answers}, indent=1), encoding="utf-8")
        print(f"recorded answers: {rec.relative_to(ROOT)}")
    print(f"\nagreement {pct(m['agreement'])} | apply precision {pct(m['apply_precision'])} | "
          f"apply recall {pct(m['apply_recall'])} | buried {m['buried']} | pushed {m['pushed']} | errors {m['errors']}")
    print(f"report: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
