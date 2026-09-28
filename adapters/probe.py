"""
adapters/probe.py - exercise one adapter against a live board without touching sweep state.

    python adapters/probe.py <ats> '<targets.json entry as JSON>' [--detail 3] [--smoke]
    python adapters/probe.py icims_jibe '{"company":"GitHub","ats":"icims_jibe","base":"https://www.github.careers"}'
    python adapters/probe.py --company "NVIDIA"          # probe an entry already in targets.json

Writes nothing under data/ or reports/. Prints the numbers an adapter has to get right before it
goes into targets.json: row count, duplicate ids, how many rows carry a posted date, a location
and a description, how the rows fall through the title/location gates, and the detail pass on
the first gate-relevant rows.
"""
import argparse, json, sys, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sweep  # noqa: E402  (loads built-in adapters and every plugin in adapters/)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ats", nargs="?")
    ap.add_argument("target", nargs="?", help="targets.json entry as JSON")
    ap.add_argument("--company", help="use the entry already in targets.json (substring)")
    ap.add_argument("--target-file", help="read the target JSON from a file (some shells split a JSON "
                                          "argument at spaces)")
    ap.add_argument("--detail", type=int, default=3, help="detail-fetch this many gate-relevant rows")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    if sweep.PLUGIN_ERRORS:
        print("PLUGIN IMPORT ERRORS:", json.dumps(sweep.PLUGIN_ERRORS, indent=1))
    if a.company:
        ts = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))["targets"]
        hits = [t for t in ts if a.company.lower() in t["company"].lower()]
        if len(hits) != 1: sys.exit(f"--company matched {len(hits)} entries: {[t['company'] for t in hits]}")
        t = hits[0]
    elif a.target_file:
        t = json.loads(Path(a.target_file).read_text(encoding="utf-8-sig"))
        if a.ats: t.setdefault("ats", a.ats)
    else:
        if not (a.ats and a.target): ap.error("give <ats> '<json>' or --company")
        t = json.loads(a.target); t.setdefault("ats", a.ats)
    fn = sweep.ADAPTERS.get(t["ats"])
    if not fn: sys.exit(f"no adapter named {t['ats']!r}. Loaded: {sorted(sweep.ADAPTERS)}")
    g = json.loads((ROOT / "gates.json").read_text(encoding="utf-8"))

    t0 = time.time()
    rows = fn(t, smoke=a.smoke)
    secs = time.time() - t0
    keys = [r["key"] for r in rows]
    dup = len(keys) - len(set(keys))
    n = len(rows) or 1
    print(f"{t['company']} [{t['ats']}]  rows={len(rows)}  duplicate keys={dup}  {secs:.1f}s"
          f"  enumerable={t['ats'] in sweep.ENUMERABLE_ATS}  detail={'yes' if t['ats'] in sweep.DETAIL else 'no'}")
    print(f"  with posted date : {sum(1 for r in rows if r.get('posted'))}/{len(rows)}")
    print(f"  with location    : {sum(1 for r in rows if r.get('location'))}/{len(rows)}")
    print(f"  with description : {sum(1 for r in rows if r.get('description'))}/{len(rows)}")
    print(f"  with comp band   : {sum(1 for r in rows if r.get('comp'))}/{len(rows)}")
    print(f"  with url         : {sum(1 for r in rows if r.get('url'))}/{len(rows)}")
    bad = [r for r in rows if not r.get("title") or not r.get("id")]
    if bad: print(f"  !! {len(bad)} row(s) with no title or id, e.g. {bad[0]}")
    try:
        json.dumps(rows)
    except TypeError as e:
        print(f"  !! rows are not JSON-serializable: {e}")
    verdicts = Counter()
    relevant = []
    for r in rows:
        v, why = sweep.gate(r, g)
        verdicts[v if v != "FAIL" else "FAIL: " + (why[0].split(":")[0] if why else "?")] += 1
        if v in sweep.DETAIL_VERDICTS: relevant.append(r)
    print("  gates            : " + ", ".join(f"{k} {v}" for k, v in verdicts.most_common()))
    for r in rows[:3]:
        print(f"  sample  {r['id']} | {r['title'][:60]} | {r['location'][:50]} | posted {r.get('posted')} | {r['url']}")
    det = sweep.DETAIL.get(t["ats"])
    for r in (relevant or rows)[:a.detail]:
        if not det: break
        try:
            det(t, r)
            print(f"  detail  {r['id']} | desc {len(r.get('description') or '')} chars | posted {r.get('posted')}"
                  f" | loc {r['location'][:50]} | comp {sweep.comp_from_description(r.get('description')) or r.get('comp')}"
                  f" | gate {sweep.gate(r, g)[0]}")
        except Exception as e:
            print(f"  !! detail failed on {r['id']}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
