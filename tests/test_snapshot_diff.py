"""Tests for anomaly drift comparison between quality snapshots."""

import unittest

from data_quality import (
    InvalidSnapshotInputError,
    UnknownSnapshotReferenceError,
)
from data_quality import compare_quality_snapshots as _compare_quality_snapshots


def compare_quality_snapshots(baseline, current, graph):
    return _compare_quality_snapshots(
        {"baseline": baseline, "current": current, "lineage": graph}
    )


def result(rule_id, dataset_id, field_id, sample_id, violated=True, value=None):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
        "violated": violated,
        "value": value,
    }


def snapshot(results):
    return {"results": results}


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def field_ref(dataset_id, field_id):
    return {"dataset_id": dataset_id, "field_id": field_id}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def lineage(datasets, fields, edges):
    return {"datasets": datasets, "fields": fields, "edges": edges}


# Forward direction: ods.name -> dwd.label -> ads.label.
BASE_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {"ods": ["name"], "dwd": ["label"], "ads": ["label"]},
    [
        edge("dwd", "label", "ods", "name", "upstream"),
        edge("dwd", "label", "ads", "label", "downstream"),
    ],
)

DIAMOND_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {
        "ods": ["a"],
        "dwd": ["x", "y"],
        "ads": ["t"],
    },
    [
        edge("ods", "a", "dwd", "x", "downstream"),
        edge("ods", "a", "dwd", "y", "downstream"),
        edge("dwd", "x", "ads", "t", "downstream"),
        edge("dwd", "y", "ads", "t", "downstream"),
    ],
)


class SnapshotSummaryTest(unittest.TestCase):
    def test_empty_snapshots(self):
        self.assertEqual(
            compare_quality_snapshots(
                snapshot([]), snapshot([]), BASE_LINEAGE
            ),
            {
                "status": "ok",
                "summary": {
                    "baseline_violation_count": 0,
                    "current_violation_count": 0,
                    "new_count": 0,
                    "resolved_count": 0,
                    "persistent_count": 0,
                },
                "changes": [],
            },
        )

    def test_counts_only_include_violated_results(self):
        baseline = snapshot(
            [
                result("r1", "ods", "name", "s1", value="x"),
                result("r2", "ods", "name", "s2", violated=False, value="ok"),
            ]
        )
        current = snapshot(
            [
                result("r1", "ods", "name", "s1", value="x"),
                result("r2", "ods", "name", "s2", violated=True, value="bad"),
                result("r3", "dwd", "label", "s2", value=9),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        self.assertEqual(
            report["summary"],
            {
                "baseline_violation_count": 1,
                "current_violation_count": 3,
                "new_count": 2,
                "resolved_count": 0,
                "persistent_count": 1,
            },
        )


class SnapshotChangeStateTest(unittest.TestCase):
    def test_new_resolved_persisted_values(self):
        baseline = snapshot(
            [
                result("r1", "ods", "name", "s1", value="old"),
                result("r2", "dwd", "label", "s1", value="gone"),
            ]
        )
        current = snapshot(
            [
                result("r1", "ods", "name", "s1", value="new"),
                result("r3", "ads", "label", "s1", value="fresh"),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        changes = report["changes"]

        by_identity = {
            (c["rule_id"], c["dataset_id"], c["field_id"], c["sample_id"]): c
            for c in changes
        }
        persisted = by_identity[("r1", "ods", "name", "s1")]
        resolved = by_identity[("r2", "dwd", "label", "s1")]
        new = by_identity[("r3", "ads", "label", "s1")]

        self.assertEqual(persisted["state"], "persisted")
        self.assertEqual(persisted["baseline_value"], "old")
        self.assertEqual(persisted["current_value"], "new")

        self.assertEqual(resolved["state"], "resolved")
        self.assertEqual(resolved["baseline_value"], "gone")
        self.assertIsNone(resolved["current_value"])

        self.assertEqual(new["state"], "new")
        self.assertIsNone(new["baseline_value"])
        self.assertEqual(new["current_value"], "fresh")

        for change in changes:
            self.assertEqual(
                set(change),
                {
                    "state",
                    "rule_id",
                    "dataset_id",
                    "field_id",
                    "sample_id",
                    "baseline_value",
                    "current_value",
                    "upstream_changes",
                    "paths",
                },
            )

    def test_changes_sorted_by_identity_regardless_of_state(self):
        baseline = snapshot(
            [
                result("rz", "ads", "label", "s1", value=1),
                result("rb", "ods", "name", "s1", value=1),
            ]
        )
        current = snapshot(
            [
                result("rz", "ads", "label", "s1", value=1),
                result("ra", "dwd", "label", "s1", value=2),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        identities = [
            (c["rule_id"], c["dataset_id"], c["field_id"], c["sample_id"])
            for c in report["changes"]
        ]
        self.assertEqual(
            identities,
            sorted(identities),
        )
        self.assertEqual(
            [c["state"] for c in report["changes"]],
            ["new", "resolved", "persisted"],
        )

    def test_deterministic_and_input_not_modified(self):
        baseline = snapshot(
            [result("r1", "ods", "name", "s1", value="x")]
        )
        current = snapshot(
            [result("r2", "dwd", "label", "s1", value="y")]
        )
        import copy

        baseline_copy = copy.deepcopy(baseline)
        current_copy = copy.deepcopy(current)
        first = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        second = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        self.assertEqual(first, second)
        self.assertEqual(baseline, baseline_copy)
        self.assertEqual(current, current_copy)


class SnapshotUpstreamChangesTest(unittest.TestCase):
    def test_upstream_changes_follow_value_changes_and_reachability(self):
        # ods.name persisted but its value moves old -> new; dwd.label is
        # new downstream of it; ads.label is resolved further downstream.
        baseline = snapshot(
            [
                result("r-up", "ods", "name", "s1", value="old"),
                result("r-down", "ads", "label", "s1", value="was-bad"),
            ]
        )
        current = snapshot(
            [
                result("r-up", "ods", "name", "s1", value="new"),
                result("r-mid", "dwd", "label", "s1", value="now-bad"),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        changes = {
            c["rule_id"]: c for c in report["changes"]
        }

        # The new dwd.label anomaly sees the changed upstream ods.name.
        mid = changes["r-mid"]
        self.assertEqual(
            mid["upstream_changes"],
            [
                {
                    "rule_id": "r-up",
                    "dataset_id": "ods",
                    "field_id": "name",
                    "sample_id": "s1",
                }
            ],
        )
        self.assertEqual(
            mid["paths"],
            [[field_ref("ods", "name"), field_ref("dwd", "label")]],
        )

        # The resolved ads.label anomaly sees both the new dwd.label
        # (null -> value counts as a change) and the persisted, changed
        # ods.name, nearest first.
        down = changes["r-down"]
        self.assertEqual(
            [(u["rule_id"]) for u in down["upstream_changes"]],
            ["r-mid", "r-up"],
        )
        self.assertEqual(
            down["paths"],
            [
                [field_ref("dwd", "label"), field_ref("ads", "label")],
                [
                    field_ref("ods", "name"),
                    field_ref("dwd", "label"),
                    field_ref("ads", "label"),
                ],
            ],
        )

        # The upstream anomaly itself has no upstream evidence.
        up = changes["r-up"]
        self.assertEqual(up["upstream_changes"], [])
        self.assertEqual(up["paths"], [])

    def test_other_samples_unreachable_fields_and_equal_values_excluded(self):
        baseline = snapshot(
            [
                # Same field, same value on both sides: no value change.
                result("r-stable", "ods", "name", "s1", value="same"),
                # A changed anomaly, but on another sample.
                result("r-other", "ods", "name", "s2", value="a"),
            ]
        )
        current = snapshot(
            [
                result("r-stable", "ods", "name", "s1", value="same"),
                result("r-other", "ods", "name", "s2", value="b"),
                result("r-new", "dwd", "label", "s1", value="bad"),
                # Downstream anomaly with no upstream value movement.
                result("r-fresh", "ads", "label", "s9", value="bad"),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        changes = {c["rule_id"]: c for c in report["changes"]}
        self.assertEqual(changes["r-new"]["upstream_changes"], [])
        self.assertEqual(changes["r-fresh"]["upstream_changes"], [])

    def test_json_boolean_is_not_equal_to_number(self):
        # 1 -> true is a value change under JSON semantics.
        baseline = snapshot(
            [result("r-up", "ods", "name", "s1", value=1)]
        )
        current = snapshot(
            [
                result("r-up", "ods", "name", "s1", value=True),
                result("r-mid", "dwd", "label", "s1", value="x"),
            ]
        )
        report = compare_quality_snapshots(baseline, current, BASE_LINEAGE)
        mid = {c["rule_id"]: c for c in report["changes"]}["r-mid"]
        self.assertEqual(
            [u["rule_id"] for u in mid["upstream_changes"]],
            ["r-up"],
        )

    def test_self_loop_does_not_link_same_field_anomalies(self):
        looped_lineage = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "label", "downstream")],
        )
        baseline = snapshot(
            [result("r1", "dwd", "label", "s1", value="a")]
        )
        current = snapshot(
            [
                result("r1", "dwd", "label", "s1", value="b"),
                result("r2", "dwd", "label", "s1", value="c"),
            ]
        )
        report = compare_quality_snapshots(
            baseline, current, looped_lineage
        )
        changes = {c["rule_id"]: c for c in report["changes"]}
        self.assertEqual(changes["r2"]["upstream_changes"], [])
        self.assertEqual(changes["r2"]["paths"], [])

    def test_shortest_path_tie_prefers_lexicographic_sequence(self):
        baseline = snapshot(
            [result("r-up", "ods", "a", "s1", value=1)]
        )
        current = snapshot(
            [
                result("r-up", "ods", "a", "s1", value=2),
                result("r-target", "ads", "t", "s1", value="x"),
            ]
        )
        report = compare_quality_snapshots(
            baseline, current, DIAMOND_LINEAGE
        )
        target = {c["rule_id"]: c for c in report["changes"]}["r-target"]
        self.assertEqual(
            [u["rule_id"] for u in target["upstream_changes"]],
            ["r-up"],
        )
        self.assertEqual(
            target["paths"],
            [
                [
                    field_ref("ods", "a"),
                    field_ref("dwd", "x"),
                    field_ref("ads", "t"),
                ]
            ],
        )


class SnapshotInputErrorTest(unittest.TestCase):
    def assert_invalid(self, baseline, current, graph=BASE_LINEAGE):
        with self.assertRaises(InvalidSnapshotInputError):
            compare_quality_snapshots(baseline, current, graph)

    def test_snapshots_must_be_objects_with_exactly_results(self):
        good = snapshot([])
        self.assert_invalid([], good)
        self.assert_invalid("nope", good)
        self.assert_invalid({}, good)
        self.assert_invalid({"results": [], "extra": 1}, good)
        self.assert_invalid(good, [])
        self.assert_invalid(good, {})
        self.assert_invalid(good, {"results": [], "extra": 1})

    def test_results_follow_correlate_contract(self):
        good = snapshot([])
        self.assert_invalid(snapshot("nope"), good)
        self.assert_invalid(
            snapshot(
                [
                    {
                        "rule_id": "r1",
                        "dataset_id": "ods",
                        "field_id": "name",
                        "sample_id": "s1",
                        "violated": True,
                    }
                ]
            ),
            good,
        )
        self.assert_invalid(
            snapshot(
                [result("r1", "ods", "name", "s1", violated="yes")]
            ),
            good,
        )
        self.assert_invalid(
            snapshot(
                [
                    result("r1", "ods", "name", "s1", value="a"),
                    result("r1", "ods", "name", "s1", value="b"),
                ]
            ),
            good,
        )

    def test_malformed_lineage(self):
        self.assert_invalid(snapshot([]), snapshot([]), {"datasets": []})
        self.assert_invalid(
            snapshot([]),
            snapshot([]),
            {"datasets": ["ods"], "fields": {"ods": ["name"]}},
        )

    def test_validation_order_structure_before_references(self):
        # Malformed current structure wins over an unknown baseline ref.
        bad_current = {"results": "nope"}
        unknown_baseline = snapshot(
            [result("r1", "ghost", "name", "s1")]
        )
        with self.assertRaises(InvalidSnapshotInputError):
            compare_quality_snapshots(
                unknown_baseline, bad_current, BASE_LINEAGE
            )

    def test_payload_must_be_object_with_exactly_three_keys(self):
        with self.assertRaises(InvalidSnapshotInputError):
            _compare_quality_snapshots([])
        with self.assertRaises(InvalidSnapshotInputError):
            _compare_quality_snapshots(
                {"baseline": snapshot([]), "current": snapshot([])}
            )
        with self.assertRaises(InvalidSnapshotInputError):
            _compare_quality_snapshots(
                {
                    "baseline": snapshot([]),
                    "current": snapshot([]),
                    "lineage": BASE_LINEAGE,
                    "extra": 1,
                }
            )


class SnapshotReferenceErrorTest(unittest.TestCase):
    def test_unknown_dataset_and_field_in_either_snapshot(self):
        good = snapshot([])
        with self.assertRaises(UnknownSnapshotReferenceError):
            compare_quality_snapshots(
                snapshot([result("r1", "ghost", "name", "s1")]),
                good,
                BASE_LINEAGE,
            )
        with self.assertRaises(UnknownSnapshotReferenceError):
            compare_quality_snapshots(
                good,
                snapshot([result("r1", "ods", "ghost", "s1")]),
                BASE_LINEAGE,
            )


if __name__ == "__main__":
    unittest.main()
