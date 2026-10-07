"""Tests for comparison of two field lineage snapshots."""

import unittest

from data_quality import (
    InvalidLineageDiffQueryError,
    InvalidLineageSnapshotError,
    UnknownLineageDiffTargetError,
    compare_lineage_snapshots,
)


def field(table, column):
    return {"table": table, "column": column}


def target(table, column):
    return {"table": table, "column": column}


def edge(source, target_):
    return {"source": source, "target": target_}


def snapshot(fields, edges):
    return {"fields": fields, "edges": edges}


def payload(baseline, current, targets):
    return {"baseline": baseline, "current": current, "targets": targets}


def empty_changes():
    return {
        "field_added": [],
        "field_removed": [],
        "edge_added": [],
        "edge_removed": [],
    }


# a -> x -> y, a -> y (shortcut)
BASELINE = snapshot(
    {"t": ["a", "x", "y"]},
    [
        edge(field("t", "x"), field("t", "a")),
        edge(field("t", "y"), field("t", "x")),
        edge(field("t", "y"), field("t", "a")),
    ],
)


class LineageDiffIdenticalTest(unittest.TestCase):
    def test_identical_snapshots(self):
        out = compare_lineage_snapshots(
            payload(BASELINE, BASELINE, [target("t", "a")])
        )
        self.assertEqual(
            out["summary"],
            {
                "added_field_count": 0,
                "removed_field_count": 0,
                "added_edge_count": 0,
                "removed_edge_count": 0,
                "changed_target_count": 0,
                "unchanged_target_count": 1,
            },
        )
        self.assertEqual(out["changes"], empty_changes())
        (result,) = out["targets"]
        self.assertEqual(result["field"], field("t", "a"))
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(
            result["upstream_delta"],
            {
                "added_fields": [],
                "removed_fields": [],
                "added_edges": [],
                "removed_edges": [],
            },
        )
        self.assertEqual(
            result["downstream_delta"],
            {
                "added_fields": [],
                "removed_fields": [],
                "added_edges": [],
                "removed_edges": [],
            },
        )

    def test_input_order_does_not_matter(self):
        # Columns and edges reordered: the graph is identical, so no
        # change may be reported.
        current = snapshot(
            {"t": ["y", "a", "x"]},
            list(reversed(BASELINE["edges"])),
        )
        out = compare_lineage_snapshots(
            payload(BASELINE, current, [target("t", "x"), target("t", "y")])
        )
        self.assertEqual(out["changes"], empty_changes())
        self.assertEqual(
            [t["status"] for t in out["targets"]],
            ["unchanged", "unchanged"],
        )

    def test_top_level_and_entry_key_order(self):
        out = compare_lineage_snapshots(
            payload(BASELINE, BASELINE, [target("t", "a")])
        )
        self.assertEqual(
            list(out), ["status", "summary", "changes", "targets"]
        )
        self.assertEqual(
            list(out["summary"]),
            [
                "added_field_count",
                "removed_field_count",
                "added_edge_count",
                "removed_edge_count",
                "changed_target_count",
                "unchanged_target_count",
            ],
        )
        self.assertEqual(
            list(out["targets"][0]),
            ["field", "status", "upstream_delta", "downstream_delta"],
        )
        self.assertEqual(
            list(out["targets"][0]["upstream_delta"]),
            ["added_fields", "removed_fields", "added_edges", "removed_edges"],
        )


class LineageDiffGlobalChangesTest(unittest.TestCase):
    def test_fields_and_edges_grouped_and_sorted(self):
        current = snapshot(
            {
                "t": ["a", "x", "y"],
                "m": ["p", "q"],
            },
            [
                edge(field("t", "x"), field("t", "a")),
                edge(field("m", "q"), field("m", "p")),
                edge(field("m", "p"), field("t", "y")),
            ],
        )
        out = compare_lineage_snapshots(
            payload(BASELINE, current, [target("t", "a")])
        )
        self.assertEqual(out["summary"]["added_field_count"], 2)
        self.assertEqual(out["summary"]["removed_field_count"], 0)
        self.assertEqual(out["summary"]["added_edge_count"], 2)
        self.assertEqual(out["summary"]["removed_edge_count"], 2)
        self.assertEqual(
            out["changes"],
            {
                "field_added": [
                    {"field": field("m", "p")},
                    {"field": field("m", "q")},
                ],
                "field_removed": [],
                "edge_added": [
                    {"edge": edge(field("m", "p"), field("t", "y"))},
                    {"edge": edge(field("m", "q"), field("m", "p"))},
                ],
                "edge_removed": [
                    {"edge": edge(field("t", "y"), field("t", "a"))},
                    {"edge": edge(field("t", "y"), field("t", "x"))},
                ],
            },
        )
        self.assertEqual(
            list(out["changes"]),
            ["field_added", "field_removed", "edge_added", "edge_removed"],
        )
        self.assertEqual(
            list(out["changes"]["field_added"][0]), ["field"]
        )
        self.assertEqual(list(out["changes"]["edge_added"][0]), ["edge"])

    def test_fields_kept_on_both_sides_are_not_changes(self):
        current = snapshot(dict(BASELINE["fields"]), [BASELINE["edges"][0]])
        out = compare_lineage_snapshots(
            payload(BASELINE, current, [target("t", "a")])
        )
        self.assertEqual(out["summary"]["added_field_count"], 0)
        self.assertEqual(out["summary"]["removed_field_count"], 0)
        self.assertEqual(out["summary"]["added_edge_count"], 0)
        self.assertEqual(out["summary"]["removed_edge_count"], 2)
        self.assertEqual(out["changes"]["field_added"], [])
        self.assertEqual(out["changes"]["field_removed"], [])
        self.assertEqual(out["changes"]["edge_added"], [])
        self.assertEqual(len(out["changes"]["edge_removed"]), 2)


class LineageDiffTargetStatusTest(unittest.TestCase):
    def test_target_added_on_current_side(self):
        current = snapshot(
            {"t": ["a", "z"]},
            [edge(field("t", "z"), field("t", "a"))],
        )
        out = compare_lineage_snapshots(
            payload(
                snapshot({"t": ["a"]}, []),
                current,
                [target("t", "z")],
            )
        )
        (result,) = out["targets"]
        self.assertEqual(result["status"], "added")
        self.assertEqual(result["upstream_delta"]["added_fields"], [])
        self.assertEqual(result["downstream_delta"]["removed_fields"], [])
        self.assertEqual(out["summary"]["changed_target_count"], 0)
        self.assertEqual(out["summary"]["unchanged_target_count"], 0)

    def test_target_removed_on_baseline_side(self):
        baseline = snapshot(
            {"t": ["a", "z"]},
            [edge(field("t", "a"), field("t", "z"))],
        )
        out = compare_lineage_snapshots(
            payload(
                baseline,
                snapshot({"t": ["a"]}, []),
                [target("t", "z")],
            )
        )
        (result,) = out["targets"]
        self.assertEqual(result["status"], "removed")
        self.assertEqual(
            result["upstream_delta"],
            {
                "added_fields": [],
                "removed_fields": [],
                "added_edges": [],
                "removed_edges": [],
            },
        )
        self.assertEqual(out["summary"]["changed_target_count"], 0)
        self.assertEqual(out["summary"]["unchanged_target_count"], 0)

    def test_changed_by_upstream_and_downstream_deltas(self):
        # current drops x and the edge y -> x: target a loses upstream
        # fields x,y through that branch and keeps only y via the
        # shortcut; target y loses downstream field a via that branch.
        current = snapshot(
            {"t": ["a", "y"]},
            [edge(field("t", "y"), field("t", "a"))],
        )
        out = compare_lineage_snapshots(
            payload(
                BASELINE,
                current,
                [target("t", "a"), target("t", "y")],
            )
        )
        by_target = {
            (t["field"]["table"], t["field"]["column"]): t
            for t in out["targets"]
        }
        self.assertEqual(by_target[("t", "a")]["status"], "changed")
        self.assertEqual(
            by_target[("t", "a")]["upstream_delta"]["removed_fields"],
            [field("t", "x")],
        )
        self.assertEqual(
            by_target[("t", "a")]["upstream_delta"]["removed_edges"],
            [
                edge(field("t", "x"), field("t", "a")),
                edge(field("t", "y"), field("t", "x")),
            ],
        )
        self.assertEqual(
            by_target[("t", "a")]["upstream_delta"]["added_fields"], []
        )
        self.assertEqual(
            by_target[("t", "a")]["downstream_delta"],
            {
                "added_fields": [],
                "removed_fields": [],
                "added_edges": [],
                "removed_edges": [],
            },
        )
        self.assertEqual(by_target[("t", "y")]["status"], "changed")
        self.assertEqual(
            by_target[("t", "y")]["downstream_delta"]["removed_fields"],
            [field("t", "x")],
        )
        self.assertEqual(
            by_target[("t", "y")]["downstream_delta"]["removed_edges"],
            [
                edge(field("t", "x"), field("t", "a")),
                edge(field("t", "y"), field("t", "x")),
            ],
        )
        self.assertEqual(out["summary"]["changed_target_count"], 2)
        self.assertEqual(out["summary"]["unchanged_target_count"], 0)

    def test_unrelated_added_edge_does_not_change_target(self):
        # b -> c is added in a component with no path to/from target a.
        current = snapshot(
            {"t": ["a", "x", "y", "b", "c"]},
            BASELINE["edges"]
            + [edge(field("t", "c"), field("t", "b"))],
        )
        out = compare_lineage_snapshots(
            payload(BASELINE, current, [target("t", "a")])
        )
        (result,) = out["targets"]
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(
            result["upstream_delta"]["added_fields"], []
        )
        self.assertEqual(
            result["downstream_delta"]["added_fields"], []
        )
        self.assertEqual(out["summary"]["unchanged_target_count"], 1)

    def test_induced_edge_appears_only_when_both_ends_reachable(self):
        # b is declared on both sides; current adds the edge b -> x.
        # From target a (upstream) both x and b are reachable, so the
        # induced edge b -> x is reported. From target b (downstream) a
        # becomes reachable through x, and exactly the induced edges
        # among {b, x, a} are reported (y -> x / y -> a stay out because
        # y is not reachable from b).
        baseline = snapshot(
            {"t": ["a", "x", "y", "b"]},
            list(BASELINE["edges"]),
        )
        current = snapshot(
            {"t": ["a", "x", "y", "b"]},
            BASELINE["edges"]
            + [edge(field("t", "b"), field("t", "x"))],
        )
        out = compare_lineage_snapshots(
            payload(baseline, current, [target("t", "a"), target("t", "b")])
        )
        by_target = {
            (t["field"]["table"], t["field"]["column"]): t
            for t in out["targets"]
        }
        self.assertEqual(by_target[("t", "a")]["status"], "changed")
        self.assertEqual(
            by_target[("t", "a")]["upstream_delta"]["added_fields"],
            [field("t", "b")],
        )
        self.assertEqual(
            by_target[("t", "a")]["upstream_delta"]["added_edges"],
            [edge(field("t", "b"), field("t", "x"))],
        )
        self.assertEqual(by_target[("t", "b")]["status"], "changed")
        self.assertEqual(
            by_target[("t", "b")]["downstream_delta"]["added_fields"],
            [field("t", "a"), field("t", "x")],
        )
        self.assertEqual(
            by_target[("t", "b")]["downstream_delta"]["added_edges"],
            [
                edge(field("t", "b"), field("t", "x")),
                edge(field("t", "x"), field("t", "a")),
            ],
        )
        self.assertEqual(
            by_target[("t", "b")]["upstream_delta"]["added_fields"], []
        )

    def test_isolated_field_on_both_sides_is_unchanged(self):
        baseline = snapshot(
            {"t": ["a", "x", "y", "b"]},
            list(BASELINE["edges"]),
        )
        # b stays isolated: its closures are identical to the baseline.
        out = compare_lineage_snapshots(
            payload(baseline, baseline, [target("t", "b")])
        )
        self.assertEqual(out["targets"][0]["status"], "unchanged")

    def test_self_loop_counts_as_reachable_edge(self):
        baseline = snapshot(
            {"t": ["a"]},
            [edge(field("t", "a"), field("t", "a"))],
        )
        current = snapshot({"t": ["a"]}, [])
        out = compare_lineage_snapshots(
            payload(baseline, current, [target("t", "a")])
        )
        (result,) = out["targets"]
        self.assertEqual(result["status"], "changed")
        removed = [
            edge(field("t", "a"), field("t", "a")),
        ]
        self.assertEqual(
            result["upstream_delta"]["removed_edges"], removed
        )
        self.assertEqual(
            result["downstream_delta"]["removed_edges"], removed
        )

    def test_cycle_is_traversed(self):
        baseline = snapshot(
            {"t": ["a", "b"]},
            [
                edge(field("t", "b"), field("t", "a")),
                edge(field("t", "a"), field("t", "b")),
            ],
        )
        current = snapshot(
            {"t": ["a", "b"]},
            [edge(field("t", "b"), field("t", "a"))],
        )
        out = compare_lineage_snapshots(
            payload(baseline, current, [target("t", "a"), target("t", "b")])
        )
        self.assertTrue(
            all(t["status"] == "changed" for t in out["targets"])
        )

    def test_targets_keep_query_order(self):
        # z is added but stays isolated, so no existing target's
        # upstream/downstream closure changes.
        current = snapshot(
            {"t": ["a", "x", "y", "z"]},
            list(BASELINE["edges"]),
        )
        out = compare_lineage_snapshots(
            payload(
                BASELINE,
                current,
                [
                    target("t", "z"),
                    target("t", "a"),
                    target("t", "y"),
                    target("t", "x"),
                ],
            )
        )
        self.assertEqual(
            [(t["field"]["table"], t["field"]["column"]) for t in out["targets"]],
            [("t", "z"), ("t", "a"), ("t", "y"), ("t", "x")],
        )
        statuses = {
            (t["field"]["table"], t["field"]["column"]): t["status"]
            for t in out["targets"]
        }
        self.assertEqual(statuses[("t", "z")], "added")
        self.assertEqual(statuses[("t", "a")], "unchanged")
        self.assertEqual(statuses[("t", "y")], "unchanged")
        self.assertEqual(statuses[("t", "x")], "unchanged")
        self.assertEqual(out["summary"]["unchanged_target_count"], 3)


class LineageDiffInvalidSnapshotTest(unittest.TestCase):
    def assert_invalid(self, bad):
        with self.assertRaises(InvalidLineageSnapshotError):
            compare_lineage_snapshots(bad)

    def test_payload_must_be_object(self):
        self.assert_invalid([])
        self.assert_invalid("nope")
        self.assert_invalid(None)

    def test_top_level_keys_exact(self):
        good = payload(BASELINE, BASELINE, [target("t", "a")])
        bad = dict(good)
        del bad["targets"]
        self.assert_invalid(bad)
        bad = dict(good)
        bad["extra"] = 1
        self.assert_invalid(bad)

    def test_snapshots_must_be_fields_edges_objects(self):
        bad = payload([], BASELINE, [target("t", "a")])
        self.assert_invalid(bad)
        bad = payload(
            snapshot({"t": ["a"]}, []),
            {"fields": {"t": ["a"]}, "edges": [], "extra": 1},
            [target("t", "a")],
        )
        self.assert_invalid(bad)
        bad = payload(
            snapshot({"t": ["a"]}, []),
            {"edges": []},
            [target("t", "a")],
        )
        self.assert_invalid(bad)

    def test_malformed_fields(self):
        self.assert_invalid(
            payload(
                snapshot([], []),
                BASELINE,
                [target("t", "a")],
            )
        )
        self.assert_invalid(
            payload(
                snapshot({"t": ["a", "a"]}, []),
                BASELINE,
                [target("t", "a")],
            )
        )
        self.assert_invalid(
            payload(
                snapshot({"": ["a"]}, []),
                BASELINE,
                [target("t", "a")],
            )
        )

    def test_malformed_edges(self):
        self.assert_invalid(
            payload(
                BASELINE,
                snapshot(
                    {"t": ["a"]},
                    [
                        edge(field("t", "a"), field("t", "ghost")),
                    ],
                ),
                [target("t", "a")],
            )
        )
        self.assert_invalid(
            payload(
                BASELINE,
                snapshot(
                    {"t": ["a"]},
                    [
                        edge(field("t", "a"), field("t", "a")),
                        edge(field("t", "a"), field("t", "a")),
                    ],
                ),
                [target("t", "a")],
            )
        )
        self.assert_invalid(
            payload(
                BASELINE,
                snapshot(
                    {"t": ["a"]},
                    [{"source": field("t", "a"), "target": field("t", "a"),
                      "type": "upstream"}],
                ),
                [target("t", "a")],
            )
        )


class LineageDiffInvalidQueryTest(unittest.TestCase):
    def assert_invalid(self, bad):
        with self.assertRaises(InvalidLineageDiffQueryError):
            compare_lineage_snapshots(bad)

    def test_targets_must_be_nonempty_list(self):
        self.assert_invalid(payload(BASELINE, BASELINE, []))
        self.assert_invalid(payload(BASELINE, BASELINE, {}))
        self.assert_invalid(payload(BASELINE, BASELINE, None))

    def test_target_shape(self):
        self.assert_invalid(payload(BASELINE, BASELINE, ["a"]))
        self.assert_invalid(
            payload(BASELINE, BASELINE, [{"table": "t"}])
        )
        self.assert_invalid(
            payload(
                BASELINE,
                BASELINE,
                [{"table": "t", "column": "a", "extra": 1}],
            )
        )
        self.assert_invalid(
            payload(BASELINE, BASELINE, [{"table": "", "column": "a"}])
        )
        self.assert_invalid(
            payload(BASELINE, BASELINE, [{"table": "t", "column": ""}])
        )
        self.assert_invalid(
            payload(BASELINE, BASELINE, [{"table": 1, "column": "a"}])
        )
        self.assert_invalid(
            payload(BASELINE, BASELINE, [{"table": "t", "column": True}])
        )

    def test_duplicate_targets_rejected(self):
        self.assert_invalid(
            payload(
                BASELINE,
                BASELINE,
                [target("t", "a"), target("t", "a")],
            )
        )


class LineageDiffUnknownTargetTest(unittest.TestCase):
    def test_target_on_neither_side(self):
        with self.assertRaises(UnknownLineageDiffTargetError):
            compare_lineage_snapshots(
                payload(
                    BASELINE,
                    BASELINE,
                    [target("t", "a"), target("ghost", "z")],
                )
            )

    def test_target_on_one_side_is_known(self):
        out = compare_lineage_snapshots(
            payload(
                snapshot({"t": ["a"]}, []),
                snapshot({"t": ["a", "z"]}, []),
                [target("t", "z")],
            )
        )
        self.assertEqual(out["targets"][0]["status"], "added")
        out = compare_lineage_snapshots(
            payload(
                snapshot({"t": ["a", "z"]}, []),
                snapshot({"t": ["a"]}, []),
                [target("t", "z")],
            )
        )
        self.assertEqual(out["targets"][0]["status"], "removed")

    def test_error_classes_and_code(self):
        self.assertTrue(issubclass(InvalidLineageSnapshotError, ValueError))
        self.assertTrue(issubclass(InvalidLineageDiffQueryError, ValueError))
        self.assertTrue(
            issubclass(UnknownLineageDiffTargetError, ValueError)
        )
        self.assertEqual(
            InvalidLineageSnapshotError.code, "INVALID_LINEAGE_SNAPSHOT"
        )
        self.assertEqual(
            InvalidLineageDiffQueryError.code, "INVALID_LINEAGE_DIFF_QUERY"
        )
        self.assertEqual(
            UnknownLineageDiffTargetError.code,
            "UNKNOWN_LINEAGE_DIFF_TARGET",
        )


class LineageDiffValidationOrderTest(unittest.TestCase):
    def test_snapshots_before_targets_shape(self):
        # Malformed baseline and empty targets: snapshot error wins.
        with self.assertRaises(InvalidLineageSnapshotError):
            compare_lineage_snapshots(
                payload(snapshot([], []), BASELINE, [])
            )

    def test_baseline_before_current(self):
        with self.assertRaises(InvalidLineageSnapshotError):
            compare_lineage_snapshots(
                payload(
                    snapshot({"t": ["a", "a"]}, []),
                    snapshot({"t": ["a", "a"]}, []),
                    [target("t", "a")],
                )
            )

    def test_targets_shape_before_existence(self):
        with self.assertRaises(InvalidLineageDiffQueryError):
            compare_lineage_snapshots(
                payload(BASELINE, BASELINE, [])
            )

    def test_unknown_target_only_after_structure_valid(self):
        with self.assertRaises(UnknownLineageDiffTargetError):
            compare_lineage_snapshots(
                payload(
                    BASELINE,
                    BASELINE,
                    [target("ghost", "a")],
                )
            )


if __name__ == "__main__":
    unittest.main()
