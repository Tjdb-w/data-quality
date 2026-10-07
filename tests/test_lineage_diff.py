"""Tests for field lineage snapshot comparison."""

import copy
import json
import unittest

from data_quality import (
    InvalidLineageDiffQueryError,
    InvalidLineageSnapshotError,
    UnknownLineageDiffTargetError,
    compare_lineage_snapshots,
)


def field(table, column):
    return {"table": table, "column": column}


def edge(src_table, src_column, dst_table, dst_column):
    return {
        "source": field(src_table, src_column),
        "target": field(dst_table, dst_column),
    }


def snapshot(fields, edges):
    return {"fields": fields, "edges": edges}


GRAPH = {
    "ods": ["a"],
    "mid": ["m"],
    "dwd": ["x", "y"],
    "ads": ["z"],
}

# ods.a -> mid.m -> dwd.x -> ads.z ; dwd.y stands alone.
EDGES = [
    edge("ods", "a", "mid", "m"),
    edge("mid", "m", "dwd", "x"),
    edge("dwd", "x", "ads", "z"),
]

BASE = snapshot(GRAPH, EDGES)


def payload(baseline, current, targets):
    return {"baseline": baseline, "current": current, "targets": targets}


class LineageDiffSuccessTest(unittest.TestCase):
    def test_identical_snapshots_unchanged(self):
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), copy.deepcopy(BASE),
                    [field("dwd", "x")])
        )
        self.assertEqual(out["status"], "ok")
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
        self.assertEqual(out["changes"], [])
        report = out["targets"][0]
        self.assertEqual(
            report,
            {
                "field": field("dwd", "x"),
                "status": "unchanged",
                "upstream_delta": {
                    "added_fields": [],
                    "removed_fields": [],
                    "added_edges": [],
                    "removed_edges": [],
                },
                "downstream_delta": {
                    "added_fields": [],
                    "removed_fields": [],
                    "added_edges": [],
                    "removed_edges": [],
                },
            },
        )

    def test_added_field_and_edge_at_snapshot_level(self):
        current = snapshot(
            {
                "ods": ["a"],
                "mid": ["m"],
                "dwd": ["x", "y"],
                "ads": ["z", "w"],
            },
            EDGES + [edge("ads", "w", "ads", "z")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "y")])
        )
        self.assertEqual(out["summary"]["added_field_count"], 1)
        self.assertEqual(out["summary"]["removed_field_count"], 0)
        self.assertEqual(out["summary"]["added_edge_count"], 1)
        self.assertEqual(out["summary"]["removed_edge_count"], 0)
        self.assertEqual(
            out["changes"],
            [
                {"type": "field_added", "field": field("ads", "w")},
                {
                    "type": "edge_added",
                    "edge": edge("ads", "w", "ads", "z"),
                },
            ],
        )
        # dwd.y is isolated; a change elsewhere keeps it unchanged.
        self.assertEqual(out["targets"][0]["status"], "unchanged")
        self.assertEqual(out["summary"]["changed_target_count"], 0)
        self.assertEqual(out["summary"]["unchanged_target_count"], 1)

    def test_removed_field_and_edge_at_snapshot_level(self):
        current = snapshot(
            {
                "ods": ["a"],
                "mid": ["m"],
                "dwd": ["x", "y"],
            },
            [edge("ods", "a", "mid", "m"), edge("mid", "m", "dwd", "x")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "y")])
        )
        self.assertEqual(out["summary"]["removed_field_count"], 1)
        self.assertEqual(out["summary"]["removed_edge_count"], 1)
        self.assertEqual(
            out["changes"],
            [
                {"type": "field_removed", "field": field("ads", "z")},
                {
                    "type": "edge_removed",
                    "edge": edge("dwd", "x", "ads", "z"),
                },
            ],
        )

    def test_change_order_groups_kinds(self):
        current = snapshot(
            {
                "mid": ["m"],
                "dwd": ["x", "y", "q"],
                "ads": ["z"],
            },
            [
                edge("mid", "m", "dwd", "x"),
                edge("dwd", "x", "ads", "z"),
                edge("dwd", "q", "dwd", "x"),
            ],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "y")])
        )
        kinds = [change["type"] for change in out["changes"]]
        self.assertEqual(
            kinds,
            ["field_added", "edge_added", "field_removed", "edge_removed"],
        )

    def test_target_added_one_sided_deltas(self):
        current = snapshot(
            {
                "ods": ["a"],
                "mid": ["m"],
                "dwd": ["x", "y"],
                "ads": ["z"],
                "raw": ["r"],
            },
            EDGES + [edge("raw", "r", "ods", "a")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("raw", "r")])
        )
        report = out["targets"][0]
        self.assertEqual(report["status"], "added")
        # An added target reports the whole current reachable component.
        self.assertEqual(report["upstream_delta"]["added_fields"], [field("raw", "r")])
        self.assertEqual(
            report["downstream_delta"]["added_fields"],
            [
                field("ads", "z"),
                field("dwd", "x"),
                field("mid", "m"),
                field("ods", "a"),
                field("raw", "r"),
            ],
        )
        self.assertEqual(
            report["downstream_delta"]["added_edges"],
            [
                edge("dwd", "x", "ads", "z"),
                edge("mid", "m", "dwd", "x"),
                edge("ods", "a", "mid", "m"),
                edge("raw", "r", "ods", "a"),
            ],
        )
        for key in ("removed_fields", "removed_edges"):
            self.assertEqual(report["upstream_delta"][key], [])
            self.assertEqual(report["downstream_delta"][key], [])
        self.assertEqual(out["summary"]["changed_target_count"], 1)
        self.assertEqual(out["summary"]["unchanged_target_count"], 0)

    def test_target_removed_one_sided_deltas(self):
        current = snapshot(
            {"ods": ["a"], "mid": ["m"], "dwd": ["x", "y"]},
            [edge("ods", "a", "mid", "m"), edge("mid", "m", "dwd", "x")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("ads", "z")])
        )
        report = out["targets"][0]
        self.assertEqual(report["status"], "removed")
        self.assertEqual(
            report["upstream_delta"]["removed_fields"],
            [
                field("ads", "z"),
                field("dwd", "x"),
                field("mid", "m"),
                field("ods", "a"),
            ],
        )
        self.assertEqual(
            report["upstream_delta"]["removed_edges"],
            [
                edge("dwd", "x", "ads", "z"),
                edge("mid", "m", "dwd", "x"),
                edge("ods", "a", "mid", "m"),
            ],
        )
        self.assertEqual(report["downstream_delta"]["removed_fields"], [field("ads", "z")])
        self.assertEqual(report["downstream_delta"]["removed_edges"], [])

    def test_changed_by_upstream_reachable_field(self):
        # A new upstream source raw.r reaches dwd.x; downstream is untouched.
        current = snapshot(
            dict(GRAPH, raw=["r"]),
            EDGES + [edge("raw", "r", "ods", "a")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "x")])
        )
        report = out["targets"][0]
        self.assertEqual(report["status"], "changed")
        self.assertEqual(
            report["upstream_delta"]["added_fields"], [field("raw", "r")]
        )
        self.assertEqual(
            report["upstream_delta"]["added_edges"],
            [edge("raw", "r", "ods", "a")],
        )
        self.assertEqual(
            report["downstream_delta"],
            {
                "added_fields": [],
                "removed_fields": [],
                "added_edges": [],
                "removed_edges": [],
            },
        )

    def test_changed_by_downstream_only(self):
        # A new sink fed from ads.z changes downstream of dwd.x and ods.a,
        # never their upstream side.
        current = snapshot(
            dict(GRAPH, rpt=["p"]),
            EDGES + [edge("ads", "z", "rpt", "p")],
        )
        out = compare_lineage_snapshots(
            payload(
                copy.deepcopy(BASE),
                current,
                [field("dwd", "x"), field("ads", "z")],
            )
        )
        dwd_x, ads_z = out["targets"]
        self.assertEqual(dwd_x["status"], "changed")
        self.assertEqual(dwd_x["upstream_delta"]["added_fields"], [])
        self.assertEqual(
            dwd_x["downstream_delta"]["added_fields"], [field("rpt", "p")]
        )
        self.assertEqual(
            dwd_x["downstream_delta"]["added_edges"],
            [edge("ads", "z", "rpt", "p")],
        )
        self.assertEqual(ads_z["status"], "changed")
        self.assertEqual(ads_z["upstream_delta"]["added_fields"], [])
        self.assertEqual(
            ads_z["downstream_delta"]["added_fields"], [field("rpt", "p")]
        )

    def test_changed_by_induced_edge_with_same_reachable_fields(self):
        # Extra shortcut ods.a -> dwd.x adds no reachable field upstream of
        # dwd.x, but the induced edge set differs.
        current = snapshot(dict(GRAPH), EDGES + [edge("ods", "a", "dwd", "x")])
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "x")])
        )
        report = out["targets"][0]
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["upstream_delta"]["added_fields"], [])
        self.assertEqual(
            report["upstream_delta"]["added_edges"],
            [edge("ods", "a", "dwd", "x")],
        )

    def test_far_away_edge_does_not_change_target(self):
        # dwd.y is isolated; changes around ads.z must not touch it.
        current = snapshot(
            dict(GRAPH, rpt=["p"]),
            EDGES + [edge("ads", "z", "rpt", "p")],
        )
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, [field("dwd", "y")])
        )
        self.assertEqual(out["targets"][0]["status"], "unchanged")
        self.assertEqual(out["summary"]["changed_target_count"], 0)
        self.assertEqual(out["summary"]["unchanged_target_count"], 1)

    def test_self_loop_counts_as_induced_edge_two_sided(self):
        # The field exists on both sides, so reachable fields are
        # identical; the removed self-loop still changes the induced
        # edge set in both directions.
        baseline = snapshot({"dwd": ["x"]}, [edge("dwd", "x", "dwd", "x")])
        current = snapshot({"dwd": ["x"]}, [])
        out = compare_lineage_snapshots(
            payload(baseline, current, [field("dwd", "x")])
        )
        report = out["targets"][0]
        self.assertEqual(report["status"], "changed")
        for side in ("upstream_delta", "downstream_delta"):
            self.assertEqual(report[side]["removed_fields"], [])
            self.assertEqual(report[side]["added_fields"], [])
            self.assertEqual(
                report[side]["removed_edges"],
                [edge("dwd", "x", "dwd", "x")],
            )

    def test_targets_preserve_order_and_counts(self):
        current = snapshot(
            dict(GRAPH, rpt=["p"]),
            EDGES + [edge("ads", "z", "rpt", "p")],
        )
        targets = [field("dwd", "y"), field("ads", "z"), field("dwd", "x")]
        out = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current, targets)
        )
        self.assertEqual(
            [report["field"] for report in out["targets"]], targets
        )
        self.assertEqual(
            [report["status"] for report in out["targets"]],
            ["unchanged", "changed", "changed"],
        )
        self.assertEqual(out["summary"]["changed_target_count"], 2)
        self.assertEqual(out["summary"]["unchanged_target_count"], 1)

    def test_output_does_not_depend_on_input_order(self):
        # Reversed table/column/edge declaration order yields the same
        # sorted result.
        reversed_graph = {
            "rpt": ["p"],
            "ads": ["z"],
            "dwd": ["y", "x"],
            "mid": ["m"],
            "ods": ["a"],
        }
        current_a = snapshot(
            dict(GRAPH, rpt=["p"]),
            EDGES + [edge("ads", "z", "rpt", "p")],
        )
        current_b = snapshot(
            reversed_graph,
            list(reversed(EDGES)) + [edge("ads", "z", "rpt", "p")],
        )
        targets = [field("dwd", "y"), field("ads", "z"), field("ods", "a")]
        out_a = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current_a, targets)
        )
        out_b = compare_lineage_snapshots(
            payload(copy.deepcopy(BASE), current_b, list(reversed(targets)))
        )
        self.assertEqual(
            json.dumps(out_a, sort_keys=True),
            json.dumps(
                {
                    **out_b,
                    "targets": list(reversed(out_b["targets"])),
                },
                sort_keys=True,
            ),
        )

    def test_key_order(self):
        out = compare_lineage_snapshots(
            payload(
                copy.deepcopy(BASE), copy.deepcopy(BASE), [field("dwd", "x")]
            )
        )
        self.assertEqual(list(out), ["status", "summary", "changes", "targets"])
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
        report = out["targets"][0]
        self.assertEqual(
            list(report),
            ["field", "status", "upstream_delta", "downstream_delta"],
        )
        self.assertEqual(
            list(report["upstream_delta"]),
            [
                "added_fields",
                "removed_fields",
                "added_edges",
                "removed_edges",
            ],
        )


class LineageDiffInvalidSnapshotTest(unittest.TestCase):
    def assert_invalid(self, bad):
        with self.assertRaises(InvalidLineageSnapshotError):
            compare_lineage_snapshots(bad)

    def test_payload_must_be_object(self):
        self.assert_invalid([])
        self.assert_invalid("nope")
        self.assert_invalid(None)

    def test_top_level_keys_exact(self):
        good = payload(copy.deepcopy(BASE), copy.deepcopy(BASE),
                       [field("dwd", "x")])
        bad = dict(good)
        del bad["current"]
        self.assert_invalid(bad)
        bad = dict(good)
        bad["extra"] = 1
        self.assert_invalid(bad)

    def test_snapshot_keys_exact(self):
        good = payload(copy.deepcopy(BASE), copy.deepcopy(BASE),
                       [field("dwd", "x")])
        bad = copy.deepcopy(good)
        bad["baseline"] = {"fields": GRAPH}
        self.assert_invalid(bad)
        bad = copy.deepcopy(good)
        bad["current"] = {"fields": GRAPH, "edges": EDGES, "extra": 1}
        self.assert_invalid(bad)
        bad = copy.deepcopy(good)
        bad["baseline"] = []
        self.assert_invalid(bad)

    def test_snapshot_graph_validation(self):
        # Field structure problems.
        self.assert_invalid(
            payload(snapshot({"ods": [1]}, []), copy.deepcopy(BASE),
                    [field("ods", "a")])
        )
        self.assert_invalid(
            payload(snapshot({"": ["a"]}, []), copy.deepcopy(BASE),
                    [field("dwd", "x")])
        )
        self.assert_invalid(
            payload(
                snapshot({"ods": ["a", "a"]}, []),
                copy.deepcopy(BASE),
                [field("dwd", "x")],
            )
        )
        # Edge problems.
        self.assert_invalid(
            payload(
                snapshot({"ods": ["a"]}, [{"source": field("ods", "a")}]),
                copy.deepcopy(BASE),
                [field("dwd", "x")],
            )
        )
        self.assert_invalid(
            payload(
                snapshot(
                    {"ods": ["a"]},
                    [{"source": field("ods", "a"),
                      "target": field("ghost", "g")}],
                ),
                copy.deepcopy(BASE),
                [field("dwd", "x")],
            )
        )
        self.assert_invalid(
            payload(
                snapshot(
                    {"ods": ["a"]},
                    [
                        edge("ods", "a", "ods", "a"),
                        edge("ods", "a", "ods", "a"),
                    ],
                ),
                copy.deepcopy(BASE),
                [field("dwd", "x")],
            )
        )

    def test_snapshots_validated_independently(self):
        # A field may exist in only one snapshot; that is never a
        # structural error on either side.
        out = compare_lineage_snapshots(
            payload(
                snapshot({"ods": ["a"]}, []),
                snapshot({"dwd": ["x"]}, []),
                [field("ods", "a"), field("dwd", "x")],
            )
        )
        self.assertEqual(
            [report["status"] for report in out["targets"]],
            ["removed", "added"],
        )


class LineageDiffInvalidQueryTest(unittest.TestCase):
    def assert_invalid_query(self, bad):
        with self.assertRaises(InvalidLineageDiffQueryError):
            compare_lineage_snapshots(bad)

    def test_targets_must_be_non_empty_list(self):
        good_graph = copy.deepcopy(BASE)
        self.assert_invalid_query(
            payload(copy.deepcopy(BASE), good_graph, [])
        )
        self.assert_invalid_query(
            payload(copy.deepcopy(BASE), copy.deepcopy(BASE), None)
        )
        self.assert_invalid_query(
            payload(copy.deepcopy(BASE), copy.deepcopy(BASE), "targets")
        )

    def test_target_shape(self):
        self.assert_invalid_query(
            payload(copy.deepcopy(BASE), copy.deepcopy(BASE), ["x"])
        )
        self.assert_invalid_query(
            payload(
                copy.deepcopy(BASE), copy.deepcopy(BASE),
                [{"table": "dwd"}],
            )
        )
        self.assert_invalid_query(
            payload(
                copy.deepcopy(BASE), copy.deepcopy(BASE),
                [{"table": "dwd", "column": "x", "extra": 1}],
            )
        )
        self.assert_invalid_query(
            payload(
                copy.deepcopy(BASE), copy.deepcopy(BASE),
                [{"table": "", "column": "x"}],
            )
        )
        self.assert_invalid_query(
            payload(
                copy.deepcopy(BASE), copy.deepcopy(BASE),
                [{"table": "dwd", "column": 1}],
            )
        )

    def test_duplicate_targets_rejected(self):
        self.assert_invalid_query(
            payload(
                copy.deepcopy(BASE),
                copy.deepcopy(BASE),
                [field("dwd", "x"), field("dwd", "x")],
            )
        )


class LineageDiffUnknownTargetTest(unittest.TestCase):
    def test_target_missing_from_both_snapshots(self):
        with self.assertRaises(UnknownLineageDiffTargetError):
            compare_lineage_snapshots(
                payload(
                    copy.deepcopy(BASE), copy.deepcopy(BASE),
                    [field("ghost", "g")],
                )
            )

    def test_first_unknown_target_reported_in_query_order(self):
        with self.assertRaises(UnknownLineageDiffTargetError):
            compare_lineage_snapshots(
                payload(
                    copy.deepcopy(BASE),
                    copy.deepcopy(BASE),
                    [field("dwd", "x"), field("ghost", "g")],
                )
            )

    def test_target_in_either_snapshot_is_known(self):
        out = compare_lineage_snapshots(
            payload(
                snapshot({"ods": ["a"]}, []),
                snapshot({"dwd": ["x"]}, []),
                [field("ods", "a"), field("dwd", "x")],
            )
        )
        self.assertEqual(
            [report["status"] for report in out["targets"]],
            ["removed", "added"],
        )

    def test_error_precedence(self):
        # Structural snapshot error beats a query-shape error.
        with self.assertRaises(InvalidLineageSnapshotError):
            compare_lineage_snapshots(
                {
                    "baseline": snapshot({"ods": [1]}, []),
                    "current": copy.deepcopy(BASE),
                    "targets": "not-a-list",
                }
            )
        # Query-shape error beats an unknown target.
        with self.assertRaises(InvalidLineageDiffQueryError):
            compare_lineage_snapshots(
                {
                    "baseline": copy.deepcopy(BASE),
                    "current": copy.deepcopy(BASE),
                    "targets": [{"table": "ghost", "column": "g", "x": 1}],
                }
            )

    def test_error_classes(self):
        self.assertTrue(issubclass(InvalidLineageSnapshotError, ValueError))
        self.assertTrue(issubclass(InvalidLineageDiffQueryError, ValueError))
        self.assertTrue(issubclass(UnknownLineageDiffTargetError, ValueError))


if __name__ == "__main__":
    unittest.main()
