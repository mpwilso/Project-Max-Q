"""targets.json integrity. Offline: reads the file, never a board.

Two employer names on one board (a subsidiary and its parent sharing a Greenhouse slug) read, gate
and count every req twice under two keys, and anything recorded against one key leaves its twin
looking untouched."""
import json, re, unittest
from collections import defaultdict

from tests.stubs import ROOT, sweep

TARGETS = json.loads((ROOT / "targets.json").read_text(encoding="utf-8-sig"))["targets"]
# Everything that is not part of where the board lives.
NOT_IDENTITY = {"company", "tier", "verified", "_note", "_added_from"}


def board_identity(t):
    return json.dumps({k: (v.lower() if isinstance(v, str) else v) for k, v in t.items() if k not in NOT_IDENTITY},
                      sort_keys=True)


class Targets(unittest.TestCase):
    def test_no_board_is_listed_twice(self):
        seen = defaultdict(list)
        for t in TARGETS: seen[board_identity(t)].append(t["company"])
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(dupes, {}, "the same board under two employer names reads every req twice")

    def test_company_names_are_unique_however_they_are_spelled(self):
        # The req key is ats:company:id, and checkpoints, read_log and coverage are keyed on the name.
        seen = defaultdict(list)
        for t in TARGETS: seen[re.sub(r"[^a-z0-9]", "", t["company"].lower())].append(t["company"])
        self.assertEqual({k: v for k, v in seen.items() if len(v) > 1}, {})

    def test_every_target_names_an_adapter_that_exists(self):
        missing = sorted({t["ats"] for t in TARGETS} - set(sweep.ADAPTERS))
        self.assertEqual(missing, [], "a target on an ATS with no adapter is never read")

    def test_every_target_has_the_fields_the_sweep_reads(self):
        bad = [t.get("company") for t in TARGETS if not t.get("company") or not t.get("ats") or "verified" not in t]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
