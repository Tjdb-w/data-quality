"""Tests for cross-sample linked origin/impact evidence analysis."""

import unittest

from data_quality import (
    LinkedOriginInputError,
    LinkedOriginReferenceError,
    LinkedOriginTargetError,
    analyze_linked_origins,
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


def field_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def sample_node(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_node(left_dataset, left_sample),
        "right": sample_node(right_dataset, right_sample),
    }


def lineage(datasets, fields, edges):
    return {"datasets": datasets, "fields": fields, "edges": edges}


def target(dataset_id, field_id, sample_id):
    return {
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
    }


# ods.name -> dwd.label -> ads.label, plus isolated dwd.id.
BASE_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {"ods": ["id", "name"], "dwd": ["id", "label"], "ads": ["label"]},
    [
        edge("dwd", "label", "ods", "name", "upstream"),
        edge("dwd", "label", "ads", "label", "downstream"),
    ],
)


class LinkedOriginEmptyTest(unittest.TestCase):
    def test_empty_targets_yield_empty_reports(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        self.assertEqual(
            analyze_linked_origins(results, BASE_LINEAGE, [], []),
            {"reports": []},
        )

    def test_empty_everything(self):
        self.assertEqual(
            analyze_linked_origins([], BASE_LINEAGE, [], []),
            {"reports": []},
        )

    def test_no_evidence_yields_empty_lists(self):
        # No links: another sample's violation cannot be reached and the
        # isolated target field has no lineage neighbour.
        results = [
            result("r1", "dwd", "id", "s1", value=0),
            result("r2", "ods", "name", "s2", value="bad"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, [], [target("dwd", "id", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r1"]
        )
        self.assertEqual(report["impactEvidence"], [])


class LinkedOriginEvidenceTest(unittest.TestCase):
    def test_self_violation_in_target_sample(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, [], [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"],
            [
                {
                    "rule_id": "r1",
                    "dataset_id": "dwd",
                    "field_id": "label",
                    "sample_id": "s1",
                    "violated": True,
                    "value": "bad",
                    "sample_path": [sample_node("dwd", "s1")],
                    "path": [field_node("dwd", "label")],
                }
            ],
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_same_sample_origin_and_impact_split(self):
        # One dataset: the same (dataset, sample) ref witnesses the
        # upstream, target and downstream fields.
        graph = lineage(
            ["dwd"],
            {"dwd": ["up", "label", "down"]},
            [
                edge("dwd", "up", "dwd", "label", "downstream"),
                edge("dwd", "label", "dwd", "down", "downstream"),
            ],
        )
        results = [
            result("r1", "dwd", "up", "s1", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "dwd", "down", "s1", value="z"),
        ]
        report = analyze_linked_origins(
            results, graph, [], [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r3"]
        )
        for item in report["originEvidence"] + report["impactEvidence"]:
            self.assertEqual(
                item["sample_path"], [sample_node("dwd", "s1")]
            )

    def test_cross_sample_upstream_origin_evidence(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "s2", value="x"),
        ]
        links = [link("dwd", "s1", "ods", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        evidence = report["originEvidence"][1]
        self.assertEqual(evidence["rule_id"], "r1")
        self.assertEqual(
            evidence["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ods", "s2")],
        )
        self.assertEqual(
            evidence["path"],
            [field_node("ods", "name"), field_node("dwd", "label")],
        )
        # ods.name is upstream only, never impact.
        self.assertEqual(report["impactEvidence"], [])

    def test_cross_sample_downstream_impact_evidence(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ads", "label", "s2", value="z"),
        ]
        links = [link("dwd", "s1", "ads", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["impactEvidence"]), 1)
        evidence = report["impactEvidence"][0]
        self.assertEqual(evidence["rule_id"], "r1")
        self.assertEqual(
            evidence["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ads", "s2")],
        )
        self.assertEqual(
            evidence["path"],
            [field_node("dwd", "label"), field_node("ads", "label")],
        )
        # ads.label is downstream only, never origin.
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r0"]
        )

    def test_same_field_in_other_linked_sample_is_both(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "dwd", "label", "s2", value="z"),
        ]
        links = [link("dwd", "s1", "dwd", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        other_origin = [
            item for item in report["originEvidence"]
            if item["sample_id"] == "s2"
        ]
        other_impact = [
            item for item in report["impactEvidence"]
            if item["sample_id"] == "s2"
        ]
        self.assertEqual(len(other_origin), 1)
        self.assertEqual(len(other_impact), 1)
        for evidence in (other_origin[0], other_impact[0]):
            self.assertEqual(
                evidence["path"], [field_node("dwd", "label")]
            )
            self.assertEqual(
                evidence["sample_path"],
                [sample_node("dwd", "s1"), sample_node("dwd", "s2")],
            )
        # The target sample's own result stays origin-only.
        own_impact = [
            item for item in report["impactEvidence"]
            if item["sample_id"] == "s1"
        ]
        self.assertEqual(own_impact, [])

    def test_unconnected_sample_provides_no_evidence(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "s2", value="x"),
            result("r2", "ads", "label", "s3", value="z"),
            result("r3", "dwd", "label", "s4", value="w"),
        ]
        # s1 sits isolated; s2-s3-s4 are linked among themselves.
        links = [
            link("ods", "s2", "ads", "s3"),
            link("ads", "s3", "dwd", "s4"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r0"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_transitive_sample_link_path(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("rm", "dwd", "id", "s2", violated=False, value=0),
            result("r1", "ods", "name", "s3", value="x"),
        ]
        links = [
            link("dwd", "s1", "dwd", "s2"),
            link("dwd", "s2", "ods", "s3"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        evidence = report["originEvidence"][1]
        self.assertEqual(
            evidence["sample_path"],
            [
                sample_node("dwd", "s1"),
                sample_node("dwd", "s2"),
                sample_node("ods", "s3"),
            ],
        )

    def test_passing_results_ignored(self):
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "s2", violated=False, value="ok"),
        ]
        links = [link("dwd", "s1", "ods", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r0"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_self_loop_no_self_impact(self):
        loop_lineage = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "label", "downstream")],
        )
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_linked_origins(
            results, loop_lineage, [], [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        self.assertEqual(report["impactEvidence"], [])

    def test_cycle_field_across_samples(self):
        # a -> b -> c -> a: from a, c is upstream via one edge and
        # downstream via two edges.
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
            result("r2", "dwd", "c", "s2", value=2),
        ]
        links = [link("dwd", "s1", "dwd", "s2")]
        report = analyze_linked_origins(
            results, cycle_lineage, links, [target("dwd", "a", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r1", "r2"],
        )
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [field_node("dwd", "c"), field_node("dwd", "a")],
        )
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r2"]
        )
        self.assertEqual(
            report["impactEvidence"][0]["path"],
            [
                field_node("dwd", "a"),
                field_node("dwd", "b"),
                field_node("dwd", "c"),
            ],
        )


class LinkedOriginPathSelectionTest(unittest.TestCase):
    def test_sample_path_lexicographic_tie_break(self):
        # s1 reaches e through two different samples in two edges:
        # (dwd, m1) sorts before (dwd, m2).
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "e", value="x"),
            result("rm1", "dwd", "id", "m1", value=1),
            result("rm2", "dwd", "id", "m2", value=2),
        ]
        links = [
            link("dwd", "s1", "dwd", "m1"),
            link("dwd", "m1", "ods", "e"),
            link("dwd", "s1", "dwd", "m2"),
            link("dwd", "m2", "ods", "e"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        evidence = next(
            item for item in report["originEvidence"]
            if item["rule_id"] == "r1"
        )
        self.assertEqual(
            evidence["sample_path"],
            [
                sample_node("dwd", "s1"),
                sample_node("dwd", "m1"),
                sample_node("ods", "e"),
            ],
        )

    def test_shorter_sample_path_wins_over_lexicographic(self):
        # The direct link s1-e beats the two-edge route through the
        # lexicographically smaller (dwd, m1).
        results = [
            result("r0", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "e", value="x"),
            result("rm1", "dwd", "id", "m1", value=1),
        ]
        links = [
            link("dwd", "s1", "ods", "e"),
            link("dwd", "s1", "dwd", "m1"),
            link("dwd", "m1", "ods", "e"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        evidence = next(
            item for item in report["originEvidence"]
            if item["rule_id"] == "r1"
        )
        self.assertEqual(
            evidence["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ods", "e")],
        )

    def test_field_path_lexicographic_tie_break(self):
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
            result("r1", "ods", "name", "s2", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
        ]
        links = [link("dwd", "s1", "ods", "s2")]
        report = analyze_linked_origins(
            results, graph, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [
                field_node("ods", "name"),
                field_node("dwd", "a"),
                field_node("dwd", "label"),
            ],
        )


class LinkedOriginSortTest(unittest.TestCase):
    def test_sorted_by_field_path_then_sample_then_rule(self):
        results = [
            result("r-self", "dwd", "label", "s1", value=0),
            result("r-other", "dwd", "label", "s2", value=0),
            result("r-up", "ods", "name", "s1", value=0),
            result("r-down", "ads", "label", "s3", value=0),
        ]
        # (ods, s1) and (dwd, s1) share the sample id but are distinct
        # references joined by an explicit link.
        links = [
            link("dwd", "s1", "dwd", "s2"),
            link("dwd", "s1", "ods", "s1"),
            link("dwd", "s1", "ads", "s3"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        # Origin: path length 1 (self s1, same field s2), then length 2
        # (ods.name); samples s1 < s2 order the length-1 pair.
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r-self", "r-other", "r-up"],
        )
        # Impact: path length 1 (same field in s2), then length 2
        # (ads.label s3).
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]],
            ["r-other", "r-down"],
        )

    def test_multiple_rules_on_same_evidence_field(self):
        results = [
            result("r0", "dwd", "label", "s1", value=0),
            result("r-b", "ods", "name", "s2", value=1),
            result("r-a", "ods", "name", "s2", value=2),
        ]
        links = [link("dwd", "s1", "ods", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r0", "r-a", "r-b"],
        )


class LinkedOriginTargetsOrderTest(unittest.TestCase):
    def test_reports_follow_targets_order(self):
        results = [
            result("r1", "dwd", "label", "s1", value="y"),
            result("r2", "ads", "label", "s1", value="z"),
        ]
        targets = [
            target("ads", "label", "s1"),
            target("dwd", "label", "s1"),
        ]
        reports = analyze_linked_origins(
            results, BASE_LINEAGE, [], targets
        )["reports"]
        self.assertEqual(
            [report["target"] for report in reports], targets
        )

    def test_duplicate_targets_produce_duplicate_reports(self):
        results = [result("r1", "dwd", "label", "s1", value="y")]
        targets = [
            target("dwd", "label", "s1"),
            target("dwd", "label", "s1"),
        ]
        reports = analyze_linked_origins(
            results, BASE_LINEAGE, [], targets
        )["reports"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])


class LinkedOriginInputErrorTest(unittest.TestCase):
    def test_error_hierarchy_and_codes(self):
        self.assertTrue(issubclass(LinkedOriginInputError, ValueError))
        self.assertTrue(issubclass(LinkedOriginReferenceError, LookupError))
        self.assertTrue(issubclass(LinkedOriginTargetError, LookupError))
        self.assertEqual(LinkedOriginInputError.code, "INVALID_INPUT")
        self.assertEqual(
            LinkedOriginReferenceError.code, "UNKNOWN_REFERENCE"
        )
        self.assertEqual(LinkedOriginTargetError.code, "UNKNOWN_TARGET")

    def test_malformed_results_and_lineage(self):
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins("nope", BASE_LINEAGE, [], [])
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins([], "nope", [], [])

    def test_malformed_links(self):
        results = [result("r1", "dwd", "label", "s1")]
        for bad_links in (
            "nope",
            ["nope"],
            [{"left": sample_node("dwd", "s1")}],
            [
                {
                    "left": sample_node("dwd", "s1"),
                    "right": {"dataset_id": "dwd"},
                }
            ],
            [
                {
                    "left": {"dataset_id": "", "sample_id": "s1"},
                    "right": sample_node("dwd", "s2"),
                }
            ],
        ):
            with self.assertRaises(LinkedOriginInputError):
                analyze_linked_origins(
                    results, BASE_LINEAGE, bad_links, []
                )

    def test_self_link_and_duplicate_link(self):
        results = [
            result("r1", "dwd", "label", "s1"),
            result("r2", "ods", "name", "s2"),
        ]
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(
                results,
                BASE_LINEAGE,
                [link("dwd", "s1", "dwd", "s1")],
                [],
            )
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(
                results,
                BASE_LINEAGE,
                [
                    link("dwd", "s1", "ods", "s2"),
                    link("ods", "s2", "dwd", "s1"),
                ],
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
            [target("", "label", "s1")],
            [target("dwd", "label", 7)],
        ):
            with self.assertRaises(LinkedOriginInputError):
                analyze_linked_origins(
                    results, BASE_LINEAGE, [], bad_targets
                )

    def test_malformed_target_precedes_unknown_link_endpoint(self):
        results = [result("r1", "dwd", "label", "s1")]
        bad_links = [link("dwd", "s1", "ghost", "s9")]
        bad_targets = [{"dataset_id": "dwd", "field_id": "label"}]
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(
                results, BASE_LINEAGE, bad_links, bad_targets
            )


class LinkedOriginReferenceErrorTest(unittest.TestCase):
    def test_unknown_result_dataset_and_field(self):
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                [result("r1", "ghost", "label", "s1")],
                BASE_LINEAGE,
                [],
                [],
            )
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                [result("r1", "dwd", "ghost", "s1")],
                BASE_LINEAGE,
                [],
                [],
            )

    def test_unknown_link_endpoint(self):
        results = [result("r1", "dwd", "label", "s1")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                results,
                BASE_LINEAGE,
                [link("dwd", "s1", "ghost", "s2")],
                [],
            )

    def test_reference_error_precedes_target_error(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                results,
                BASE_LINEAGE,
                [],
                [target("dwd", "label", "s9")],
            )


class LinkedOriginTargetErrorTest(unittest.TestCase):
    def test_target_without_result(self):
        results = [result("r1", "dwd", "label", "s1")]
        with self.assertRaises(LinkedOriginTargetError):
            analyze_linked_origins(
                results, BASE_LINEAGE, [], [target("dwd", "label", "s9")]
            )

    def test_target_with_only_passing_result(self):
        results = [result("r1", "dwd", "label", "s1", violated=False)]
        with self.assertRaises(LinkedOriginTargetError):
            analyze_linked_origins(
                results, BASE_LINEAGE, [], [target("dwd", "label", "s1")]
            )


if __name__ == "__main__":
    unittest.main()
