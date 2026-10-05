"""Tests for violation origin/impact evidence analysis."""

import unittest

from data_quality import (
    InvalidOriginInputError,
    UnknownOriginReferenceError,
    UnknownOriginTargetError,
    analyze_violation_origins,
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


def lineage(datasets, fields, edges):
    return {"datasets": datasets, "fields": fields, "edges": edges}


def target(dataset_id, field_id, sample_id):
    return {
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
    }


# ods.name -> dwd.label -> ads.label, plus dwd.id with no edges.
BASE_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {"ods": ["id", "name"], "dwd": ["id", "label"], "ads": ["label"]},
    [
        edge("dwd", "label", "ods", "name", "upstream"),
        edge("dwd", "label", "ads", "label", "downstream"),
    ],
)


class OriginEmptyTest(unittest.TestCase):
    def test_empty_targets_yield_empty_reports(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        self.assertEqual(
            analyze_violation_origins(results, BASE_LINEAGE, []),
            {"reports": []},
        )

    def test_empty_results_and_empty_targets(self):
        self.assertEqual(
            analyze_violation_origins([], BASE_LINEAGE, []), {"reports": []}
        )

    def test_no_evidence_yields_empty_lists(self):
        # dwd.id has no lineage edges and no other violation shares s1, so
        # only its own self evidence remains and nothing is impacted.
        results = [
            result("r1", "dwd", "id", "s1", value=0),
            result("r2", "dwd", "label", "s2", value="bad"),
            result("r3", "ods", "name", "s1", violated=False, value="ok"),
        ]
        reports = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "id", "s1")]
        )["reports"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(
            reports[0]["target"], target("dwd", "id", "s1")
        )
        self.assertEqual(
            [item["rule_id"] for item in reports[0]["originEvidence"]],
            ["r1"],
        )
        self.assertEqual(reports[0]["impactEvidence"], [])


class OriginEvidenceTest(unittest.TestCase):
    def test_self_violation_is_origin_evidence(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        evidence = report["originEvidence"][0]
        self.assertEqual(
            evidence,
            {
                "rule_id": "r1",
                "dataset_id": "dwd",
                "field_id": "label",
                "sample_id": "s1",
                "violated": True,
                "value": "bad",
                "path": [path_node("dwd", "label")],
            },
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_upstream_self_and_downstream_split(self):
        results = [
            result("r1", "ods", "name", "s1", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "ads", "label", "s1", value="z"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]

        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [path_node("ods", "name"), path_node("dwd", "label")],
        )
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r3"]
        )
        self.assertEqual(
            report["impactEvidence"][0]["path"],
            [path_node("dwd", "label"), path_node("ads", "label")],
        )

    def test_transitive_upstream_path(self):
        results = [
            result("r1", "ods", "name", "s1", value="x"),
            result("r2", "ads", "label", "s1", value="z"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("ads", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [
                path_node("ods", "name"),
                path_node("dwd", "label"),
                path_node("ads", "label"),
            ],
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_other_samples_and_passing_results_are_ignored(self):
        results = [
            result("r1", "dwd", "label", "s1", value="y"),
            result("r2", "ods", "name", "s2", value="other-sample"),
            result("r3", "ods", "name", "s1", violated=False, value="ok"),
            result("r4", "dwd", "id", "s1", value="unrelated"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r1"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_multiple_rules_on_one_field(self):
        results = [
            result("r2", "ods", "name", "s1", value="x"),
            result("r1", "ods", "name", "s1", value="x"),
            result("r3", "dwd", "label", "s1", value="y"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        # Same path length and field: rule_id breaks the tie.
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r3", "r1", "r2"],
        )

    def test_self_loop_does_not_count_as_impact(self):
        loop_lineage = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "label", "downstream")],
        )
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_violation_origins(
            results, loop_lineage, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        self.assertEqual(report["originEvidence"][0]["path"],
                         [path_node("dwd", "label")])
        self.assertEqual(report["impactEvidence"], [])

    def test_cycle_field_counts_as_origin_and_impact(self):
        # dwd.a -> dwd.b -> dwd.c -> dwd.a: from dwd.a the field dwd.c is
        # upstream (one edge backwards) and downstream (two edges forward).
        cycle_lineage = lineage(
            ["dwd"],
            {"dwd": ["a", "b", "c"]},
            [
                edge("dwd", "a", "dwd", "b", "downstream"),
                edge("dwd", "b", "dwd", "c", "downstream"),
                edge("dwd", "c", "dwd", "a", "downstream"),
            ],
        )
        results = [
            result("r1", "dwd", "a", "s1", value=1),
            result("r2", "dwd", "c", "s1", value=2),
        ]
        report = analyze_violation_origins(
            results, cycle_lineage, [target("dwd", "a", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r1", "r2"],
        )
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [path_node("dwd", "c"), path_node("dwd", "a")],
        )
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r2"]
        )
        self.assertEqual(
            report["impactEvidence"][0]["path"],
            [path_node("dwd", "a"), path_node("dwd", "b"), path_node("dwd", "c")],
        )


class OriginPathSelectionTest(unittest.TestCase):
    def test_shortest_path_wins(self):
        # ods.name reaches dwd.label directly and via ods.id; the direct
        # edge is shorter.
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["id", "name"], "dwd": ["label"]},
            [
                edge("ods", "name", "dwd", "label", "downstream"),
                edge("ods", "name", "ods", "id", "downstream"),
                edge("ods", "id", "dwd", "label", "downstream"),
            ],
        )
        results = [
            result("r1", "ods", "name", "s1", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
        ]
        report = analyze_violation_origins(
            results, graph, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [path_node("ods", "name"), path_node("dwd", "label")],
        )

    def test_lexicographic_tie_break(self):
        # Two equal-length paths ods.name -> dwd.a -> dwd.label and
        # ods.name -> dwd.b -> dwd.label: the dwd.a sequence wins.
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["a", "b", "label"]},
            [
                edge("ods", "name", "dwd", "b", "downstream"),
                edge("ods", "name", "dwd", "a", "downstream"),
                edge("dwd", "a", "dwd", "label", "downstream"),
                edge("dwd", "b", "dwd", "label", "downstream"),
            ],
        )
        results = [
            result("r1", "ods", "name", "s1", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
        ]
        report = analyze_violation_origins(
            results, graph, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [
                path_node("ods", "name"),
                path_node("dwd", "a"),
                path_node("dwd", "label"),
            ],
        )

    def test_impact_path_runs_target_to_evidence(self):
        graph = lineage(
            ["dwd", "ads", "mart"],
            {"dwd": ["label"], "ads": ["label"], "mart": ["label"]},
            [
                edge("dwd", "label", "ads", "label", "downstream"),
                edge("ads", "label", "mart", "label", "downstream"),
            ],
        )
        results = [
            result("r1", "dwd", "label", "s1", value="x"),
            result("r2", "mart", "label", "s1", value="z"),
        ]
        report = analyze_violation_origins(
            results, graph, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            report["impactEvidence"][0]["path"],
            [
                path_node("dwd", "label"),
                path_node("ads", "label"),
                path_node("mart", "label"),
            ],
        )


class OriginEvidenceSortTest(unittest.TestCase):
    def test_sorted_by_path_length_then_field_then_rule(self):
        # ods.name -> dwd.label -> ads.label; dwd.id -> ads.label.
        graph = lineage(
            ["ods", "dwd", "ads"],
            {"ods": ["name"], "dwd": ["id", "label"], "ads": ["label"]},
            [
                edge("ods", "name", "dwd", "label", "downstream"),
                edge("dwd", "label", "ads", "label", "downstream"),
                edge("dwd", "id", "ads", "label", "downstream"),
            ],
        )
        results = [
            result("r4", "ods", "name", "s1", value=1),
            result("r3", "dwd", "id", "s1", value=2),
            result("r2", "dwd", "label", "s1", value=3),
            result("r1", "ads", "label", "s1", value=4),
        ]
        report = analyze_violation_origins(
            results, graph, [target("ads", "label", "s1")]
        )["reports"][0]
        # Path lengths: r1 self (1), r2/r3 one edge (2), r4 two edges (3);
        # r2 before r3 by dataset/field ("label" < "id" is false, so
        # field_id decides: dwd.id < dwd.label? dataset equal, id < label).
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r1", "r3", "r2", "r4"],
        )


class OriginTargetsOrderTest(unittest.TestCase):
    def test_reports_follow_targets_order(self):
        results = [
            result("r1", "dwd", "label", "s1", value="y"),
            result("r2", "ads", "label", "s1", value="z"),
        ]
        targets = [
            target("ads", "label", "s1"),
            target("dwd", "label", "s1"),
        ]
        reports = analyze_violation_origins(results, BASE_LINEAGE, targets)[
            "reports"
        ]
        self.assertEqual(
            [report["target"] for report in reports], targets
        )

    def test_duplicate_targets_produce_duplicate_reports(self):
        results = [result("r1", "dwd", "label", "s1", value="y")]
        targets = [target("dwd", "label", "s1"), target("dwd", "label", "s1")]
        reports = analyze_violation_origins(results, BASE_LINEAGE, targets)[
            "reports"
        ]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])


class OriginInputErrorTest(unittest.TestCase):
    def test_malformed_results(self):
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins("nope", BASE_LINEAGE, [])
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(
                [result("r1", "dwd", "label", "s1", violated="yes")],
                BASE_LINEAGE,
                [],
            )

    def test_malformed_lineage(self):
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins([], "nope", [])
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(
                [],
                lineage(["dwd"], {"dwd": ["a"]}, [edge("dwd", "a", "ghost", "b")]),
                [],
            )

    def test_malformed_targets(self):
        results = [result("r1", "dwd", "label", "s1")]
        for bad_targets in (
            "nope",
            ["nope"],
            [{"dataset_id": "dwd", "field_id": "label"}],
            [
                {
                    "dataset_id": "dwd",
                    "field_id": "label",
                    "sample_id": "s1",
                    "extra": 1,
                }
            ],
            [target("dwd", "", "s1")],
            [target("dwd", "label", 7)],
        ):
            with self.assertRaises(InvalidOriginInputError):
                analyze_violation_origins(results, BASE_LINEAGE, bad_targets)

    def test_conflicting_duplicate_results(self):
        results = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r1", "dwd", "label", "s1", value="b"),
        ]
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(results, BASE_LINEAGE, [])


class OriginReferenceErrorTest(unittest.TestCase):
    def test_unknown_dataset(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(results, BASE_LINEAGE, [])

    def test_unknown_field(self):
        results = [result("r1", "dwd", "ghost", "s1")]
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(results, BASE_LINEAGE, [])

    def test_reference_error_precedes_target_error(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "label", "s1")]
            )


class OriginTargetErrorTest(unittest.TestCase):
    def test_target_without_any_result(self):
        results = [result("r1", "dwd", "label", "s1")]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "label", "s9")]
            )

    def test_target_with_only_passing_result(self):
        results = [result("r1", "dwd", "label", "s1", violated=False)]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "label", "s1")]
            )

    def test_target_with_undeclared_field(self):
        # Undeclared fields can never match a (reference-checked) result.
        results = [result("r1", "dwd", "label", "s1")]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "ghost", "s1")]
            )


if __name__ == "__main__":
    unittest.main()
