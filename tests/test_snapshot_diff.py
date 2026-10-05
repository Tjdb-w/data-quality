"""Tests for anomaly drift comparison between quality snapshots."""

import unittest

from data_quality import (
    InvalidSnapshotInputError,
    UnknownSnapshotReferenceError,
    compare_quality_snapshots,
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


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def path_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def snapshot(*results):
    return {"results": list(results)}


def payload(baseline_results, current_results, lineage_):
    return {
        "baseline": snapshot(*baseline_results),
        "current": snapshot(*current_results),
        "lineage": lineage_,
    }


LINEAGE = {
    "datasets": ["ods", "mid", "dwd", "ads"],
    "fields": {
        "ods": ["a", "b"],
        "mid": ["c"],
        "dwd": ["x", "y"],
        "ads": ["z"],
    },
    "edges": [
        edge("mid", "c", "ods", "a", "upstream"),
        edge("mid", "c", "ods", "b", "upstream"),
        edge("dwd", "x", "mid", "c", "upstream"),
        edge("dwd", "x", "ods", "a", "upstream"),
        edge("ads", "z", "dwd", "x", "upstream"),
    ],
}


def identity(rule_id, dataset_id, field_id, sample_id):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
    }


class SnapshotEmptyTest(unittest.TestCase):
    def test_empty_snapshots(self):
        self.assertEqual(
            compare_quality_snapshots(payload([], [], LINEAGE)),
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

    def test_passing_results_produce_no_changes(self):
        out = compare_quality_snapshots(
            payload(
                [result("r1", "ods", "a", "s1", violated=False, value=1)],
                [result("r1", "ods", "a", "s1", violated=False, value=2)],
                LINEAGE,
            )
        )
        self.assertEqual(out["summary"]["baseline_violation_count"], 0)
        self.assertEqual(out["summary"]["current_violation_count"], 0)
        self.assertEqual(out["changes"], [])


class SnapshotClassificationTest(unittest.TestCase):
    def test_new_resolved_persisted(self):
        out = compare_quality_snapshots(
            payload(
                [
                    result("r-old", "ods", "a", "s1", value="gone"),
                    result("r-keep", "dwd", "x", "s1", value="same"),
                ],
                [
                    result("r-keep", "dwd", "x", "s1", value="same"),
                    result("r-new", "ads", "z", "s1", value="fresh"),
                ],
                LINEAGE,
            )
        )
        self.assertEqual(
            out["summary"],
            {
                "baseline_violation_count": 2,
                "current_violation_count": 2,
                "new_count": 1,
                "resolved_count": 1,
                "persistent_count": 1,
            },
        )
        states = {(c["rule_id"], c["state"]) for c in out["changes"]}
        self.assertEqual(
            states,
            {("r-old", "resolved"), ("r-keep", "persisted"), ("r-new", "new")},
        )

        by_rule = {c["rule_id"]: c for c in out["changes"]}
        resolved = by_rule["r-old"]
        self.assertEqual(resolved["baseline_value"], "gone")
        self.assertIsNone(resolved["current_value"])
        new = by_rule["r-new"]
        self.assertIsNone(new["baseline_value"])
        self.assertEqual(new["current_value"], "fresh")
        kept = by_rule["r-keep"]
        self.assertEqual(kept["baseline_value"], "same")
        self.assertEqual(kept["current_value"], "same")

    def test_persisted_keeps_both_original_values(self):
        out = compare_quality_snapshots(
            payload(
                [result("r1", "ods", "a", "s1", value={"k": [1, True]})],
                [result("r1", "ods", "a", "s1", value={"k": [2, False]})],
                LINEAGE,
            )
        )
        (change,) = out["changes"]
        self.assertEqual(change["state"], "persisted")
        self.assertEqual(change["baseline_value"], {"k": [1, True]})
        self.assertEqual(change["current_value"], {"k": [2, False]})

    def test_changes_sorted_by_identity_lexicographically(self):
        out = compare_quality_snapshots(
            payload(
                [result("r2", "dwd", "x", "s9", value=0)],
                [
                    result("r1", "ads", "z", "s1", value=0),
                    result("r1", "ads", "z", "s2", value=0),
                    result("r1", "mid", "c", "s3", value=0),
                ],
                LINEAGE,
            )
        )
        keys = [
            (c["rule_id"], c["dataset_id"], c["field_id"], c["sample_id"])
            for c in out["changes"]
        ]
        self.assertEqual(
            keys,
            [
                ("r1", "ads", "z", "s1"),
                ("r1", "ads", "z", "s2"),
                ("r1", "mid", "c", "s3"),
                ("r2", "dwd", "x", "s9"),
            ],
        )

    def test_change_key_order(self):
        out = compare_quality_snapshots(
            payload(
                [], [result("r1", "ods", "a", "s1", value=1)], LINEAGE
            )
        )
        self.assertEqual(
            list(out["changes"][0]),
            [
                "state",
                "rule_id",
                "dataset_id",
                "field_id",
                "sample_id",
                "baseline_value",
                "current_value",
                "upstream_changes",
                "paths",
            ],
        )
        self.assertEqual(list(out["summary"]), [
            "baseline_violation_count",
            "current_violation_count",
            "new_count",
            "resolved_count",
            "persistent_count",
        ])
        self.assertEqual(list(out), ["status", "summary", "changes"])


class SnapshotUpstreamChangesTest(unittest.TestCase):
    def test_upstream_drifting_change_with_shortest_path(self):
        # ods.a (persisted, drifted) -> mid.c -> dwd.x, with a shortcut
        # edge ods.a -> dwd.x; the shortest path wins. Target dwd.x is new.
        out = compare_quality_snapshots(
            payload(
                [result("r-a", "ods", "a", "s1", value="x")],
                [
                    result("r-a", "ods", "a", "s1", value="y"),
                    result("r-x", "dwd", "x", "s1", value="bad"),
                ],
                LINEAGE,
            )
        )
        by_rule = {c["rule_id"]: c for c in out["changes"]}
        target = by_rule["r-x"]
        self.assertEqual(
            target["upstream_changes"],
            [identity("r-a", "ods", "a", "s1")],
        )
        self.assertEqual(
            target["paths"],
            [[path_node("ods", "a"), path_node("dwd", "x")]],
        )
        # The upstream change has no upstream evidence of its own.
        self.assertEqual(by_rule["r-a"]["upstream_changes"], [])

    def test_persisted_without_value_drift_is_not_upstream_change(self):
        # ods.a persists unchanged: it is reachable upstream of the new
        # dwd.x but its value did not change, so it is not reported.
        out = compare_quality_snapshots(
            payload(
                [
                    result("r-a", "ods", "a", "s1", value="same"),
                    result("r-b", "ods", "b", "s1", value="x"),
                ],
                [
                    result("r-a", "ods", "a", "s1", value="same"),
                    result("r-b", "ods", "b", "s1", value="y"),
                    result("r-x", "dwd", "x", "s1", value="bad"),
                ],
                LINEAGE,
            )
        )
        target = next(c for c in out["changes"] if c["rule_id"] == "r-x")
        self.assertEqual(
            target["upstream_changes"],
            [identity("r-b", "ods", "b", "s1")],
        )
        self.assertEqual(
            target["paths"],
            [
                [
                    path_node("ods", "b"),
                    path_node("mid", "c"),
                    path_node("dwd", "x"),
                ]
            ],
        )

    def test_boolean_never_equals_number_for_drift(self):
        # JSON equality: true is not 1, so a true -> 1 persisted anomaly
        # counts as a value change and qualifies as upstream evidence.
        out = compare_quality_snapshots(
            payload(
                [
                    result("r-a", "ods", "a", "s1", value=True),
                ],
                [
                    result("r-a", "ods", "a", "s1", value=1),
                    result("r-x", "dwd", "x", "s1", value="bad"),
                ],
                LINEAGE,
            )
        )
        target = next(c for c in out["changes"] if c["rule_id"] == "r-x")
        self.assertEqual(
            target["upstream_changes"],
            [identity("r-a", "ods", "a", "s1")],
        )

    def test_other_sample_and_downstream_excluded(self):
        out = compare_quality_snapshots(
            payload(
                [result("r-a", "ods", "a", "s1", value=0)],
                [
                    result("r-a", "ods", "a", "s1", value=1),
                    result("r-a2", "ods", "a", "s2", value=2),
                    result("r-x", "dwd", "x", "s1", value=3),
                    result("r-z", "ads", "z", "s1", value=4),
                ],
                LINEAGE,
            )
        )
        by_rule_sample = {
            (c["rule_id"], c["sample_id"]): c for c in out["changes"]
        }
        # dwd.x: ods.a/s1 drifted upstream; ods.a/s2 is another sample;
        # ads.z sits downstream and must not appear.
        target = by_rule_sample[("r-x", "s1")]
        self.assertEqual(
            target["upstream_changes"],
            [identity("r-a", "ods", "a", "s1")],
        )
        # ads.z is downstream of both dwd.x (new) and ods.a (drifted):
        # both qualify, ordered by identity with aligned paths.
        downstream = by_rule_sample[("r-z", "s1")]
        self.assertEqual(
            downstream["upstream_changes"],
            [
                identity("r-a", "ods", "a", "s1"),
                identity("r-x", "dwd", "x", "s1"),
            ],
        )
        self.assertEqual(
            downstream["paths"],
            [
                [path_node("ods", "a"), path_node("dwd", "x"),
                 path_node("ads", "z")],
                [path_node("dwd", "x"), path_node("ads", "z")],
            ],
        )

    def test_self_loop_never_makes_self_an_upstream_change(self):
        lineage_ = {
            "datasets": ["dwd"],
            "fields": {"dwd": ["x"]},
            "edges": [edge("dwd", "x", "dwd", "x", "downstream")],
        }
        # Two rules on the same field can only reach each other through
        # the self-loop; neither may become the other's upstream change.
        out = compare_quality_snapshots(
            payload(
                [result("r1", "dwd", "x", "s1", value=0)],
                [
                    result("r1", "dwd", "x", "s1", value=1),
                    result("r2", "dwd", "x", "s1", value=2),
                ],
                lineage_,
            )
        )
        for change in out["changes"]:
            self.assertEqual(change["upstream_changes"], [])
            self.assertEqual(change["paths"], [])

    def test_upstream_changes_sorted_with_aligned_paths(self):
        # Two upstream fields reach the target through equal-length
        # chains (ods.a/ods.b -> mid.c -> dwd.x). Their anomalies carry
        # rule ids ordered opposite to field order, so the output follows
        # identity (rule id) order and paths stay aligned.
        parallel_lineage = {
            "datasets": ["ods", "mid", "dwd"],
            "fields": {
                "ods": ["a", "b"],
                "mid": ["c"],
                "dwd": ["x"],
            },
            "edges": [
                edge("mid", "c", "ods", "a", "upstream"),
                edge("mid", "c", "ods", "b", "upstream"),
                edge("dwd", "x", "mid", "c", "upstream"),
            ],
        }
        out = compare_quality_snapshots(
            payload(
                [
                    result("rz", "ods", "a", "s1", value=0),
                    result("ra", "ods", "b", "s1", value=0),
                ],
                [
                    result("rz", "ods", "a", "s1", value=1),
                    result("ra", "ods", "b", "s1", value=9),
                    result("rt", "dwd", "x", "s1", value=2),
                ],
                parallel_lineage,
            )
        )
        target = next(c for c in out["changes"] if c["rule_id"] == "rt")
        self.assertEqual(
            target["upstream_changes"],
            [
                identity("ra", "ods", "b", "s1"),
                identity("rz", "ods", "a", "s1"),
            ],
        )
        self.assertEqual(
            target["paths"],
            [
                [path_node("ods", "b"), path_node("mid", "c"),
                 path_node("dwd", "x")],
                [path_node("ods", "a"), path_node("mid", "c"),
                 path_node("dwd", "x")],
            ],
        )

    def test_new_and_resolved_count_as_drifting_upstream_evidence(self):
        # A resolved upstream anomaly and a new upstream anomaly both
        # count as value changes for the persisted target.
        out = compare_quality_snapshots(
            payload(
                [
                    result("r-a", "ods", "a", "s1", value="old"),
                    result("r-x", "dwd", "x", "s1", value="same"),
                ],
                [
                    result("r-b", "ods", "b", "s1", value="new"),
                    result("r-x", "dwd", "x", "s1", value="same"),
                ],
                LINEAGE,
            )
        )
        target = next(c for c in out["changes"] if c["rule_id"] == "r-x")
        self.assertEqual(
            target["upstream_changes"],
            [
                identity("r-a", "ods", "a", "s1"),
                identity("r-b", "ods", "b", "s1"),
            ],
        )


class SnapshotInvalidInputTest(unittest.TestCase):
    def assert_invalid(self, bad):
        with self.assertRaises(InvalidSnapshotInputError):
            compare_quality_snapshots(bad)

    def test_payload_must_be_object(self):
        self.assert_invalid([])
        self.assert_invalid("nope")

    def test_top_level_keys_exact(self):
        good = payload([], [], LINEAGE)
        bad = dict(good)
        del bad["current"]
        self.assert_invalid(bad)
        bad = dict(good)
        bad["extra"] = 1
        self.assert_invalid(bad)

    def test_snapshots_must_be_results_objects(self):
        good = payload([], [], LINEAGE)
        bad = dict(good)
        bad["baseline"] = []
        self.assert_invalid(bad)
        bad = dict(good)
        bad["current"] = {"results": [], "extra": 1}
        self.assert_invalid(bad)

    def test_malformed_lineage(self):
        bad = payload([], [], {"datasets": [], "fields": {}, "edges": []})
        bad["lineage"] = {"datasets": [], "fields": {}}
        self.assert_invalid(bad)

    def test_malformed_results(self):
        bad = payload(
            [{"rule_id": "r1"}], [], LINEAGE
        )
        self.assert_invalid(bad)
        bad = payload(
            [result("r1", "ods", "a", "", value=1)], [], LINEAGE
        )
        self.assert_invalid(bad)
        bad = payload(
            [result("r1", "ods", "a", "s1", violated="yes")], [], LINEAGE
        )
        self.assert_invalid(bad)

    def test_conflicting_duplicate_results(self):
        bad = payload(
            [
                result("r1", "ods", "a", "s1", value=1),
                result("r1", "ods", "a", "s1", value=2),
            ],
            [],
            LINEAGE,
        )
        self.assert_invalid(bad)


class SnapshotUnknownReferenceTest(unittest.TestCase):
    def test_unknown_dataset(self):
        with self.assertRaises(UnknownSnapshotReferenceError):
            compare_quality_snapshots(
                payload(
                    [], [result("r1", "ghost", "a", "s1", value=1)], LINEAGE
                )
            )

    def test_unknown_field(self):
        with self.assertRaises(UnknownSnapshotReferenceError):
            compare_quality_snapshots(
                payload(
                    [result("r1", "ods", "ghost", "s1", value=1)],
                    [],
                    LINEAGE,
                )
            )

    def test_structural_error_precedes_reference_error(self):
        # Both a structural problem and an unknown reference are present;
        # validation must report the structural one.
        bad = payload(
            [result("r1", "ghost", "a", "s1", value=1)],
            [],
            {
                "datasets": ["ods"],
                "fields": {"ods": ["a"]},
                "edges": [{"bad": 1}],
            },
        )
        with self.assertRaises(InvalidSnapshotInputError):
            compare_quality_snapshots(bad)

    def test_error_classes(self):
        self.assertTrue(issubclass(InvalidSnapshotInputError, ValueError))
        self.assertTrue(
            issubclass(UnknownSnapshotReferenceError, LookupError)
        )
        self.assertEqual(
            InvalidSnapshotInputError.code, "INVALID_SNAPSHOT_INPUT"
        )
        self.assertEqual(
            UnknownSnapshotReferenceError.code, "UNKNOWN_SNAPSHOT_REFERENCE"
        )


if __name__ == "__main__":
    unittest.main()
