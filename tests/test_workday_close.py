"""Regression tests: a Workday walk that reached its own total closes the reqs it did not return.

A complete walk is a whole-board read. Treated as a query-scoped sample instead, every missing req is
carried for 14 days, and reqs that have closed keep appearing in --window as CARRIED.

Offline: carry_forward is called directly on synthetic rows."""
import unittest

from tests.stubs import sweep

CO = "WdCo"
TARGETS = [{"company": CO, "ats": "workday"}]


def prev_row(i, **extra):
    return dict({"key": f"workday:{CO}:{i}", "company": CO, "ats": "workday", "title": f"PM {i}"}, **extra)


def run(status, complete_walks, prev_scopes=None, prev=None):
    prev = prev or {r["key"]: r for r in (prev_row(1), prev_row(2))}
    rows = {}
    coverage = [(CO, "workday", status, 0)]
    return sweep.carry_forward(rows, prev, coverage, TARGETS, complete_walks=complete_walks,
                               prev_scopes=prev_scopes), rows


class CompleteWorkdayWalkCloses(unittest.TestCase):
    def test_complete_unscoped_walk_closes_missing_reqs(self):
        carried, rows = run("OK", {CO: None})
        self.assertEqual(carried, {})
        self.assertEqual(rows, {})

    def test_incomplete_walk_still_carries_as_a_sample(self):
        carried, _ = run("OK", {})
        self.assertEqual(set(carried.values()), {sweep.CARRY_SAMPLE})

    def test_complete_scoped_walk_closes_a_row_the_last_scoped_read_returned(self):
        carried, _ = run(f"OK ({sweep.WORKDAY_SCOPE_MARK})", {CO: sweep.WORKDAY_SCOPE_LABEL},
                         prev_scopes={CO: sweep.WORKDAY_SCOPE_LABEL})
        self.assertEqual(carried, {})

    def test_first_scoped_walk_still_carries(self):
        # The last read was unscoped, so a missing row may simply be abroad.
        carried, _ = run(f"OK ({sweep.WORKDAY_SCOPE_MARK})", {CO: sweep.WORKDAY_SCOPE_LABEL}, prev_scopes={})
        self.assertEqual(set(carried.values()), {sweep.CARRY_SCOPE})

    def test_scoped_walk_keeps_carrying_rows_already_carried_as_out_of_scope(self):
        prev = {r["key"]: r for r in (prev_row(1, _carried_reason=sweep.CARRY_SCOPE, _carried_since="2026-09-22"),)}
        carried, _ = run(f"OK ({sweep.WORKDAY_SCOPE_MARK})", {CO: sweep.WORKDAY_SCOPE_LABEL},
                         prev_scopes={CO: sweep.WORKDAY_SCOPE_LABEL}, prev=prev)
        self.assertEqual(set(carried.values()), {sweep.CARRY_SCOPE})

    def test_explicit_target_facets_never_close(self):
        carried, _ = run("OK", {CO: "facets"})
        self.assertEqual(set(carried.values()), {sweep.CARRY_SAMPLE})

    def test_an_errored_read_carries_whatever_the_walk_state(self):
        carried, _ = run("ERROR Timeout", {CO: None})
        self.assertEqual(set(carried.values()), {sweep.CARRY_ERROR})


if __name__ == "__main__":
    unittest.main()
