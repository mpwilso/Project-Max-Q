"""
tools/merge_targets.py - fold probed employer entries from adapters/*.targets.json into targets.json.

    python tools/merge_targets.py            # dry run: what would be added, skipped, and why
    python tools/merge_targets.py --write    # write targets.json (a .bak copy is kept)

An entry is skipped when its company is already a target (case-insensitive) or when another target
already reads the same board (same ats + slug / tenant+site / base / host+site). Resolved employers
are removed from the `unresolved` notes. Every added entry must name an ats that sweep.py can load.
"""
import argparse, json, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def board_id(t):
    # Every key that locates a board. Without list_url/params, two avature boards with no slug
    # would look like the same board.
    keys = [t.get("ats")] + [str(t.get(k, "")).lower().rstrip("/") for k in
                             ("slug", "tenant", "site", "base", "host", "origin", "domain", "list_url",
                              "company_code", "org", "account")]
    keys.append(json.dumps(t.get("params") or {}, sort_keys=True))
    if not any(keys[1:-1]):
        keys.append(t["company"].lower())      # no known locator: never collapse two employers
    return "|".join(k or "" for k in keys)


def norm_name(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    import sweep
    tpath = ROOT / "targets.json"
    doc = json.loads(tpath.read_text(encoding="utf-8"))
    targets = doc["targets"]
    names = {norm_name(t["company"]) for t in targets}
    boards = {board_id(t) for t in targets}
    # targets.json "removed" {company: reason}: a drop made in targets.json itself also blocks the
    # probe entries still sitting in older adapters/*.targets.json files.
    removed = {norm_name(c): why for c, why in (doc.get("removed") or {}).items()}
    added, skipped = [], []
    for src in sorted((ROOT / "adapters").glob("*.targets.json")):
        for t in json.loads(src.read_text(encoding="utf-8-sig")):
            why = None
            if t.get("removed"):
                why = f"removed on purpose: {t['removed']}"
            elif norm_name(t["company"]) in removed:
                why = f"removed on purpose (targets.json): {removed[norm_name(t['company'])]}"
            elif t.get("verified") is False:
                why = "not verified live (an adapter did not exist at probe time); re-probe first"
            elif t.get("ats") not in sweep.ADAPTERS:
                why = f"no adapter named {t.get('ats')!r}"
            elif norm_name(t["company"]) in names:
                why = "company already a target"
            elif board_id(t) in boards:
                why = "same board already read by another target"
            if why:
                skipped.append((src.name, t["company"], why)); continue
            t.setdefault("tier", "C"); t.setdefault("verified", True)
            t["_added_from"] = src.name
            targets.append(t); added.append((src.name, t["company"], t["ats"]))
            names.add(norm_name(t["company"])); boards.add(board_id(t))
    resolved = {norm_name(c) for _, c, _ in added}
    before = len(doc.get("unresolved", []))
    doc["unresolved"] = [u for u in doc.get("unresolved", [])
                         if not any(norm_name(u.split("(")[0]).startswith(r) or r in norm_name(u.split("(")[0])
                                    for r in resolved if len(r) > 3)]
    for s, c, ats in added: print(f"ADD   {c:40} {ats:16} from {s}")
    for s, c, why in skipped: print(f"SKIP  {c:40} {why} ({s})")
    print(f"\n{len(added)} to add, {len(skipped)} skipped, {before - len(doc['unresolved'])} unresolved note(s) cleared; "
          f"targets {len(targets) - len(added)} -> {len(targets)}")
    if a.write and added:
        shutil.copyfile(tpath, tpath.with_suffix(".json.bak"))
        tpath.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {tpath} (backup targets.json.bak)")


if __name__ == "__main__":
    main()
