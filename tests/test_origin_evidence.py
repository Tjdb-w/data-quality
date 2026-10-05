"""Tests for violation origin/impact evidence analysis."""

import copy
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


def path(*pairs):
    return [
        {"dataset_id": dataset, "field_id": field} for dataset, field in pairs
    ]


# ods.name -> dwd.label -> ads.label, plus dwd.id with no edges.
BASE_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {"ods": ["id", "name"], "dwd": ["id", "label"], "ads": ["label"]},
    [
        edge("dwd", "label", "ods", "name", "upstream"),
        edge("dwd", "label", "ads", "label", "downstream"),
    ],
)


class ViolationOriginsEmptyTest(unittest.TestCase):
    def test_empty_targets_returns_empty_reports(self):
        results = [result("r1", "dwd", "label", "s1")]
        self.assertEqual(
            analyze_violation_origins(results, BASE_LINEAGE, []),
            {"reports": []},
        )

    def test_empty_results_and_empty_graph(self):
        self.assertEqual(
            analyze_violation_origins([], lineage([], {}, []), []),
            {"reports": []},
        )

    def test_report_keys_and_target_echo(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            set(report), {"target", "originEvidence", "impactEvidence"}
        )
        self.assertEqual(report["target"], target("dwd", "label", "s1"))


class ViolationOriginsSelfEvidenceTest(unittest.TestCase):
    def test_own_violation_is_length_zero_origin(self):
        results = [result("r1", "dwd", "id", "s1", value=9)]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "id", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        evidence = report["originEvidence"][0]
        self.assertEqual(evidence["rule_id"], "r1")
        self.assertEqual(evidence["dataset_id"], "dwd")
        self.assertEqual(evidence["field_id"], "id")
        self.assertEqual(evidence["sample_id"], "s1")
        self.assertTrue(evidence["violated"])
        self.assertEqual(evidence["value"], 9)
        self.assertEqual(evidence["path"], path(("dwd", "id")))
        self.assertEqual(report["impactEvidence"], [])

    def test_multiple_rules_on_target_field_sort_by_rule_id(self):
        results = [
            result("rZ", "dwd", "label", "s1", value=1),
            result("rA", "dwd", "label", "s1", value=2),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["rA", "rZ"]
        )
        for evidence in report["originEvidence"]:
            self.assertEqual(evidence["path"], path(("dwd", "label")))


class ViolationOriginsUpstreamTest(unittest.TestCase):
    def test_upstream_violation_is_origin_evidence(self):
        results = [
            result("rUp", "ods", "name", "s1", value="x"),
            result("rSelf", "dwd", "label", "s1", value="y"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]],
            ["rSelf", "rUp"],
        )
        upstream = report["originEvidence"][1]
        # Path runs forward from the evidence field to the target.
        self.assertEqual(
            upstream["path"], path(("ods", "name"), ("dwd", "label"))
        )
        self.assertEqual(upstream["value"], "x")
        self.assertEqual(report["impactEvidence"], [])

    def test_upstream_path_oriented_forward_to_target(self):
        # Target the top of the chain: only its own evidence, because
        # ods.name has no upstream; ads/dwd are downstream so excluded.
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "s1"),
            result("r3", "ads", "label", "s1"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("ods", "name", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["r1"]
        )
        self.assertEqual(report["originEvidence"][0]["path"], path(("ods", "name")))


class ViolationOriginsImpactTest(unittest.TestCase):
    def test_downstream_violation_is_impact_evidence(self):
        results = [
            result("rSelf", "dwd", "label", "s1", value="y"),
            result("rDown", "ads", "label", "s1", value="z"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(len(report["impactEvidence"]), 1)
        impact = report["impactEvidence"][0]
        self.assertEqual(impact["rule_id"], "rDown")
        # Path runs forward from the target to the evidence field.
        self.assertEqual(
            impact["path"], path(("dwd", "label"), ("ads", "label"))
        )
        self.assertEqual(impact["value"], "z")

    def test_self_loop_does_not_self_impact(self):
        graph = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "label", "downstream")],
        )
        results = [result("r1", "dwd", "label", "s1", value="y")]
        report = analyze_violation_origins(
            results, graph, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["r1"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_other_samples_are_not_evidence(self):
        results = [
            result("rSelf", "dwd", "label", "s1", value="y"),
            result("rUp", "ods", "name", "OTHER", value="x"),
            result("rDown", "ads", "label", "OTHER", value="z"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["rSelf"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_non_violated_results_are_not_evidence(self):
        results = [
            result("rSelf", "dwd", "label", "s1", value="y"),
            result("rUp", "ods", "name", "s1", violated=False, value="x"),
            result("rDown", "ads", "label", "s1", violated=False, value="z"),
        ]
        report = analyze_violation_origins(
            results, BASE_LINEAGE, [target("dwd", "label", "s1")]
        )["reports"][0]
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["rSelf"]
        )
        self.assertEqual(report["impactEvidence"], [])


class ViolationOriginsPathTest(unittest.TestCase):
    # Diamond: A.a -> B.b -> D.d and A.a -> C.c -> D.d (B sorts before C).
    DIAMOND = lineage(
        ["A", "B", "C", "D"],
        {"A": ["a"], "B": ["b"], "C": ["c"], "D": ["d"]},
        [
            edge("A", "a", "B", "b", "downstream"),
            edge("B", "b", "D", "d", "downstream"),
            edge("A", "a", "C", "c", "downstream"),
            edge("C", "c", "D", "d", "downstream"),
        ],
    )

    def test_shortest_path_then_lexicographic_sequence(self):
        results = [
            result("rA", "A", "a", "s"),
            result("rB", "B", "b", "s"),
            result("rC", "C", "c", "s"),
            result("rD", "D", "d", "s"),
        ]
        report = analyze_violation_origins(
            results, self.DIAMOND, [target("D", "d", "s")]
        )["reports"][0]
        by_rule = {e["rule_id"]: e["path"] for e in report["originEvidence"]}
        self.assertEqual(by_rule["rD"], path(("D", "d")))
        self.assertEqual(by_rule["rB"], path(("B", "b"), ("D", "d")))
        self.assertEqual(by_rule["rC"], path(("C", "c"), ("D", "d")))
        # Two length-2 routes to A.a; the lexicographically smaller field
        # sequence runs through B.b.
        self.assertEqual(
            by_rule["rA"],
            path(("A", "a"), ("B", "b"), ("D", "d")),
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_evidence_sorted_by_path_length_then_field_then_rule(self):
        results = [
            result("rD", "D", "d", "s"),
            result("rA", "A", "a", "s"),
            result("rB", "B", "b", "s"),
            result("rC", "C", "c", "s"),
        ]
        report = analyze_violation_origins(
            results, self.DIAMOND, [target("D", "d", "s")]
        )["reports"][0]
        ordered = [
            (
                len(e["path"]) - 1,
                e["dataset_id"],
                e["field_id"],
                e["rule_id"],
            )
            for e in report["originEvidence"]
        ]
        self.assertEqual(
            ordered,
            sorted(ordered),
        )
        self.assertEqual(
            [(d, f) for d, ds, f, r in ordered],
            [(0, "d"), (1, "b"), (1, "c"), (2, "a")],
        )

    def test_impact_paths_on_diamond(self):
        results = [
            result("rA", "A", "a", "s"),
            result("rB", "B", "b", "s"),
            result("rC", "C", "c", "s"),
            result("rD", "D", "d", "s"),
        ]
        report = analyze_violation_origins(
            results, self.DIAMOND, [target("A", "a", "s")]
        )["reports"][0]
        by_rule = {e["rule_id"]: e["path"] for e in report["impactEvidence"]}
        self.assertEqual(by_rule["rB"], path(("A", "a"), ("B", "b")))
        self.assertEqual(by_rule["rC"], path(("A", "a"), ("C", "c")))
        self.assertEqual(
            by_rule["rD"],
            path(("A", "a"), ("B", "b"), ("D", "d")),
        )
        # Own violation lives only in origin evidence.
        self.assertEqual(
            [e["rule_id"] for e in report["originEvidence"]], ["rA"]
        )


class ViolationOriginsCycleTest(unittest.TestCase):
    CYCLE = lineage(
        ["A", "B", "C"],
        {"A": ["a"], "B": ["b"], "C": ["c"]},
        [
            edge("A", "a", "B", "b", "downstream"),
            edge("B", "b", "C", "c", "downstream"),
            edge("C", "c", "A", "a", "downstream"),
        ],
    )

    def test_cycle_uses_shortest_direction(self):
        results = [
            result("rA", "A", "a", "s"),
            result("rB", "B", "b", "s"),
            result("rC", "C", "c", "s"),
        ]
        report = analyze_violation_origins(
            results, self.CYCLE, [target("A", "a", "s")]
        )["reports"][0]
        by_rule = {e["rule_id"]: e["path"] for e in report["originEvidence"]}
        # C.c is one edge upstream of A.a; B.b is two edges upstream.
        self.assertEqual(by_rule["rC"], path(("C", "c"), ("A", "a")))
        self.assertEqual(
            by_rule["rB"], path(("B", "b"), ("C", "c"), ("A", "a"))
        )
        impacts = {e["rule_id"]: e["path"] for e in report["impactEvidence"]}
        self.assertEqual(impacts["rB"], path(("A", "a"), ("B", "b")))
        self.assertEqual(
            impacts["rC"], path(("A", "a"), ("B", "b"), ("C", "c"))
        )


class ViolationOriginsTargetsTest(unittest.TestCase):
    def test_reports_follow_targets_order_including_duplicates(self):
        results = [
            result("rUp", "ods", "name", "s1"),
            result("rSelf", "dwd", "label", "s1"),
            result("rDown", "ads", "label", "s1"),
        ]
        targets = [
            target("ads", "label", "s1"),
            target("ods", "name", "s1"),
            target("ads", "label", "s1"),
        ]
        reports = analyze_violation_origins(
            results, BASE_LINEAGE, targets
        )["reports"]
        self.assertEqual(len(reports), 3)
        self.assertEqual(
            [rpt["target"]["field_id"] for rpt in reports],
            ["label", "name", "label"],
        )
        self.assertEqual(
            [rpt["target"]["dataset_id"] for rpt in reports],
            ["ads", "ods", "ads"],
        )
        # First and third report are identical.
        self.assertEqual(reports[0], reports[2])

    def test_no_violated_result_raises_target_error(self):
        results = [result("r1", "dwd", "label", "s1")]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "label", "OTHER")]
            )

    def test_non_violated_result_raises_target_error(self):
        results = [
            result("r1", "dwd", "label", "s1", violated=False, value="ok")
        ]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(
                results, BASE_LINEAGE, [target("dwd", "label", "s1")]
            )

    def test_error_aborts_before_partial_reports(self):
        # First target resolves; a later unknown target must still abort.
        results = [result("r1", "dwd", "label", "s1")]
        targets = [
            target("dwd", "label", "s1"),
            target("dwd", "label", "missing"),
        ]
        with self.assertRaises(UnknownOriginTargetError):
            analyze_violation_origins(results, BASE_LINEAGE, targets)


class ViolationOriginsTargetValidationTest(unittest.TestCase):
    RESULTS = [result("r1", "dwd", "label", "s1")]

    def assert_invalid(self, targets):
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(self.RESULTS, BASE_LINEAGE, targets)

    def test_targets_must_be_a_list(self):
        self.assert_invalid(None)
        self.assert_invalid({})
        self.assert_invalid("nope")

    def test_target_must_be_an_object(self):
        self.assert_invalid(["nope"])

    def test_target_keys_are_strict(self):
        bad = target("dwd", "label", "s1")
        del bad["sample_id"]
        self.assert_invalid([bad])

        bad = target("dwd", "label", "s1")
        bad["extra"] = 1
        self.assert_invalid([bad])

    def test_identifiers_must_be_non_empty_strings(self):
        for key in ("dataset_id", "field_id", "sample_id"):
            bad = target("dwd", "label", "s1")
            bad[key] = ""
            self.assert_invalid([bad])
            bad = target("dwd", "label", "s1")
            bad[key] = None
            self.assert_invalid([bad])
            bad = target("dwd", "label", "s1")
            bad[key] = 7
            self.assert_invalid([bad])


class ViolationOriginsReuseValidationTest(unittest.TestCase):
    TARGETS = [target("dwd", "label", "s1")]

    def test_malformed_lineage_is_invalid_origin_input(self):
        bad_graph = {"datasets": ["dwd"]}  # missing fields/edges keys
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(
                self._results(), bad_graph, self.TARGETS
            )

    def test_malformed_results_are_invalid_origin_input(self):
        bad = [result("r1", "dwd", "label", "s1")]
        bad[0]["violated"] = "yes"
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(bad, BASE_LINEAGE, self.TARGETS)

    def test_conflicting_duplicate_results_are_invalid(self):
        bad = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r1", "dwd", "label", "s1", value="b"),
        ]
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(bad, BASE_LINEAGE, self.TARGETS)

    def test_dangling_edge_is_invalid_origin_input(self):
        graph = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "ghost", "name")],
        )
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(
                self._results(), graph, self.TARGETS
            )

    @staticmethod
    def _results():
        return [result("r1", "dwd", "label", "s1")]


class ViolationOriginsReferenceTest(unittest.TestCase):
    TARGETS = [target("dwd", "label", "s1")]

    def test_unknown_dataset_raises_origin_reference_error(self):
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(
                [result("r1", "ghost", "label", "s1")],
                BASE_LINEAGE,
                self.TARGETS,
            )

    def test_unknown_field_raises_origin_reference_error(self):
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(
                [result("r1", "dwd", "ghost", "s1")],
                BASE_LINEAGE,
                self.TARGETS,
            )

    def test_non_violated_results_are_reference_checked_too(self):
        results = [
            result("r1", "dwd", "label", "s1"),
            result("r2", "ghost", "name", "s1", violated=False),
        ]
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(results, BASE_LINEAGE, self.TARGETS)

    def test_lookup_error_is_not_value_error(self):
        self.assertFalse(
            issubclass(UnknownOriginReferenceError, ValueError)
        )
        self.assertFalse(issubclass(UnknownOriginTargetError, ValueError))
        self.assertTrue(issubclass(UnknownOriginReferenceError, LookupError))
        self.assertTrue(issubclass(UnknownOriginTargetError, LookupError))
        self.assertTrue(issubclass(InvalidOriginInputError, ValueError))

    def test_validation_order_structure_then_reference_then_target(self):
        # Structural problem (bad edge) beats an unknown reference.
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["label"]},
            [edge("dwd", "label", "ods", "name", "sideways")],
        )
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(InvalidOriginInputError):
            analyze_violation_origins(
                results, graph, [target("ghost", "label", "s1")]
            )
        # Unknown reference beats an unknown target.
        with self.assertRaises(UnknownOriginReferenceError):
            analyze_violation_origins(
                [result("r1", "ghost", "label", "s1")],
                BASE_LINEAGE,
                [target("nobody", "nothing", "nowhere")],
            )


class ViolationOriginsDeterminismTest(unittest.TestCase):
    def test_input_is_not_mutated(self):
        results = [
            result("rUp", "ods", "name", "s1", value=[1, 2]),
            result("rSelf", "dwd", "label", "s1", value={"a": 1}),
        ]
        graph = copy.deepcopy(BASE_LINEAGE)
        targets = [target("dwd", "label", "s1")]
        results_snapshot = copy.deepcopy(results)
        graph_snapshot = copy.deepcopy(graph)
        targets_snapshot = copy.deepcopy(targets)
        analyze_violation_origins(results, graph, targets)
        self.assertEqual(results, results_snapshot)
        self.assertEqual(graph, graph_snapshot)
        self.assertEqual(targets, targets_snapshot)

    def test_deterministic_output(self):
        results = [
            result("rDown", "ads", "label", "s1", value="z"),
            result("rUp", "ods", "name", "s1", value="x"),
            result("rSelf", "dwd", "label", "s1", value="y"),
        ]
        targets = [target("dwd", "label", "s1")]
        first = analyze_violation_origins(results, BASE_LINEAGE, targets)
        second = analyze_violation_origins(
            list(reversed(results)), BASE_LINEAGE, targets
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
