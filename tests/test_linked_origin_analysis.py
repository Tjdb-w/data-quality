"""Tests for cross-sample violation origin/impact evidence analysis."""

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


def path_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def sample_node(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def lineage(datasets, fields, edges):
    return {"datasets": datasets, "fields": fields, "edges": edges}


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_node(left_dataset, left_sample),
        "right": sample_node(right_dataset, right_sample),
    }


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

BASE_RESULTS = [
    result("r1", "ods", "name", "o1", value="x"),
    result("r2", "dwd", "label", "s1", value="y"),
    result("r3", "ads", "label", "a1", value="z"),
]

BASE_LINKS = [
    link("dwd", "s1", "ods", "o1"),
    link("dwd", "s1", "ads", "a1"),
]

BASE_TARGET = [target("dwd", "label", "s1")]


class LinkedOriginEmptyTest(unittest.TestCase):
    def test_empty_targets_yield_empty_reports(self):
        self.assertEqual(
            analyze_linked_origins(
                BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, []
            ),
            {"reports": []},
        )

    def test_empty_everything(self):
        self.assertEqual(
            analyze_linked_origins(
                [], lineage([], {}, []), [], []
            ),
            {"reports": []},
        )

    def test_isolated_target_has_only_self_origin(self):
        # dwd.id has no edges and s9 links to nothing; o1 lives in
        # another component, so only the target's own origin remains.
        results = [
            result("r1", "dwd", "id", "s9", value=0),
            result("r2", "ods", "name", "o1", value="bad"),
        ]
        reports = analyze_linked_origins(
            results, BASE_LINEAGE, [], [target("dwd", "id", "s9")]
        )["reports"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(
            [item["rule_id"] for item in reports[0]["originEvidence"]],
            ["r1"],
        )
        self.assertEqual(reports[0]["impactEvidence"], [])


class LinkedOriginEvidenceTest(unittest.TestCase):
    def test_self_violation_is_origin_with_single_node_paths(self):
        results = [result("r2", "dwd", "label", "s1", value="y")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, [], BASE_TARGET
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        evidence = report["originEvidence"][0]
        self.assertEqual(
            evidence,
            {
                "rule_id": "r2",
                "dataset_id": "dwd",
                "field_id": "label",
                "sample_id": "s1",
                "violated": True,
                "value": "y",
                "sample_path": [sample_node("dwd", "s1")],
                "path": [path_node("dwd", "label")],
            },
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_cross_sample_origin_and_impact_split(self):
        report = analyze_linked_origins(
            BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, BASE_TARGET
        )["reports"][0]

        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        upstream = report["originEvidence"][1]
        self.assertEqual(
            upstream["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ods", "o1")],
        )
        self.assertEqual(
            upstream["path"],
            [path_node("ods", "name"), path_node("dwd", "label")],
        )

        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]],
            ["r3"],
        )
        downstream = report["impactEvidence"][0]
        self.assertEqual(
            downstream["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ads", "a1")],
        )
        self.assertEqual(
            downstream["path"],
            [path_node("dwd", "label"), path_node("ads", "label")],
        )

    def test_evidence_keeps_every_result_key_and_adds_two_paths(self):
        report = analyze_linked_origins(
            BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, BASE_TARGET
        )["reports"][0]
        for evidence in (
            report["originEvidence"] + report["impactEvidence"]
        ):
            self.assertEqual(
                set(evidence),
                {
                    "rule_id",
                    "dataset_id",
                    "field_id",
                    "sample_id",
                    "violated",
                    "value",
                    "sample_path",
                    "path",
                },
            )

    def test_target_field_on_linked_sample_is_origin_never_impact(self):
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r9", "dwd", "label", "s2", value="other"),
        ]
        links = [link("dwd", "s1", "dwd", "s2")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r9"],
        )
        linked = report["originEvidence"][1]
        self.assertEqual(
            linked["sample_path"],
            [sample_node("dwd", "s1"), sample_node("dwd", "s2")],
        )
        self.assertEqual(linked["path"], [path_node("dwd", "label")])
        self.assertEqual(report["impactEvidence"], [])

    def test_self_loop_does_not_make_target_its_own_impact(self):
        loop_lineage = lineage(
            ["dwd"],
            {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "label", "downstream")],
        )
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        report = analyze_linked_origins(
            results, loop_lineage, [], BASE_TARGET
        )["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        self.assertEqual(report["impactEvidence"], [])

    def test_downstream_field_on_linked_sample_ref_is_impact(self):
        # A different downstream field -- even one carrying the same
        # sample id in another dataset -- is impact once linked.
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "ads", "label", "s1", value="z"),
        ]
        links = [link("dwd", "s1", "ads", "s1")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r3"]
        )
        self.assertEqual(
            report["impactEvidence"][0]["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ads", "s1")],
        )

    def test_same_ref_downstream_field_is_impact_with_one_node_sample_path(
        self,
    ):
        # Same dataset+sample ref, a different downstream field is
        # ordinary impact and sample_path is the single target ref.
        graph = lineage(
            ["dwd"],
            {"dwd": ["label", "derived"]},
            [edge("dwd", "label", "dwd", "derived", "downstream")],
        )
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "dwd", "derived", "s1", value="z"),
        ]
        report = analyze_linked_origins(
            results, graph, [], BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["impactEvidence"]], ["r3"]
        )
        self.assertEqual(
            report["impactEvidence"][0]["sample_path"],
            [sample_node("dwd", "s1")],
        )
        self.assertEqual(
            report["impactEvidence"][0]["path"],
            [path_node("dwd", "label"), path_node("dwd", "derived")],
        )

    def test_unlinked_sample_is_not_evidence(self):
        # a1 has a downstream violation but no link to the target
        # component.
        links = [link("dwd", "s1", "ods", "o1")]
        report = analyze_linked_origins(
            BASE_RESULTS, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_passing_results_and_other_component_ignored(self):
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "ads", "label", "a1", violated=False, value="ok"),
            result("r1", "ods", "name", "o9", value="far"),
        ]
        links = [link("dwd", "s1", "ads", "a1")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]], ["r2"]
        )
        self.assertEqual(report["impactEvidence"], [])

    def test_multiple_rules_on_one_linked_field(self):
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "o1", value="a"),
            result("r0", "ods", "name", "o1", value="b"),
        ]
        links = [link("dwd", "s1", "ods", "o1")]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r0", "r1"],
        )
        for item in report["originEvidence"][1:]:
            self.assertEqual(item["sample_id"], "o1")

    def test_transitive_sample_path(self):
        # s1 - m1 - o1: the upstream violation on o1 is two links away;
        # the routing hop m1 only carries a passing result on a
        # lineage-unrelated field and is itself no evidence.
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("rm", "dwd", "id", "m1", violated=False, value="ok"),
            result("r1", "ods", "name", "o1", value="x"),
        ]
        links = [
            link("dwd", "s1", "dwd", "m1"),
            link("dwd", "m1", "ods", "o1"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        origin_rules = [item["rule_id"] for item in report["originEvidence"]]
        self.assertEqual(origin_rules, ["r2", "r1"])
        far = report["originEvidence"][1]
        self.assertEqual(
            far["sample_path"],
            [
                sample_node("dwd", "s1"),
                sample_node("dwd", "m1"),
                sample_node("ods", "o1"),
            ],
        )
        self.assertEqual(
            far["path"],
            [path_node("ods", "name"), path_node("dwd", "label")],
        )


class LinkedOriginPathSelectionTest(unittest.TestCase):
    def test_sample_path_shortest_wins(self):
        # s1 -> px -> o1 and a direct s1 -> o1 link: direct wins.
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "o1", value="x"),
            result("rp", "dwd", "id", "px", violated=False, value="ok"),
        ]
        links = [
            link("dwd", "s1", "ods", "o1"),
            link("dwd", "s1", "dwd", "px"),
            link("dwd", "px", "ods", "o1"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["sample_path"],
            [sample_node("dwd", "s1"), sample_node("ods", "o1")],
        )

    def test_sample_path_lexicographic_tie_break(self):
        # Two two-link routes s1-px-o1 and s1-py-o1; px sequence wins.
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "o1", value="x"),
            result("rx", "dwd", "id", "px", violated=False),
            result("ry", "dwd", "id", "py", violated=False),
        ]
        links = [
            link("dwd", "s1", "dwd", "px"),
            link("dwd", "s1", "dwd", "py"),
            link("dwd", "px", "ods", "o1"),
            link("dwd", "py", "ods", "o1"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["sample_path"],
            [
                sample_node("dwd", "s1"),
                sample_node("dwd", "px"),
                sample_node("ods", "o1"),
            ],
        )

    def test_field_path_lexicographic_tie_break(self):
        # ods.name -> dwd.a -> dwd.label and ods.name -> dwd.b -> label.
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
            result("r2", "dwd", "label", "s1", value="y"),
            result("r1", "ods", "name", "o1", value="x"),
        ]
        links = [link("dwd", "s1", "ods", "o1")]
        report = analyze_linked_origins(
            results, graph, links, BASE_TARGET
        )["reports"][0]
        self.assertEqual(
            report["originEvidence"][1]["path"],
            [
                path_node("ods", "name"),
                path_node("dwd", "a"),
                path_node("dwd", "label"),
            ],
        )

    def test_cycle_field_is_origin_and_impact_across_samples(self):
        # dwd.a -> dwd.b -> dwd.c -> dwd.a: dwd.c is upstream of a and
        # reachable downstream of it on a longer forward route.
        graph = lineage(
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
            results, graph, links, [target("dwd", "a", "s1")]
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
            [
                path_node("dwd", "a"),
                path_node("dwd", "b"),
                path_node("dwd", "c"),
            ],
        )


class LinkedOriginSortTest(unittest.TestCase):
    def test_sample_path_length_outranks_field_path_length(self):
        # o1 (1 link away, 2-edge field path) and ofar (2 links away,
        # 1-edge field path): o1 sorts first.
        results = [
            result("rt", "ads", "label", "s1", value="t"),
            result("r-near", "dwd", "label", "o1", value="n"),
            result("r-far", "ads", "label", "f1", value="f"),
            result("rm", "dwd", "id", "m1", violated=False),
        ]
        links = [
            link("ads", "s1", "dwd", "o1"),
            link("ads", "s1", "dwd", "m1"),
            link("dwd", "m1", "ads", "f1"),
        ]
        report = analyze_linked_origins(
            results,
            BASE_LINEAGE,
            links,
            [target("ads", "label", "s1")],
        )["reports"][0]
        # r-near: same target field on linked sample (origin, 1-node
        # field path), r-far: target field two links away. Both are
        # self-field origin evidence; sample path length decides.
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["rt", "r-near", "r-far"],
        )

    def test_same_field_path_length_then_identifiers(self):
        results = [
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "ads", "label", "a1", value="z"),
            result("r4", "ads", "label", "a2", value="w"),
        ]
        links = [
            link("dwd", "s1", "ads", "a1"),
            link("dwd", "s1", "ads", "a2"),
        ]
        report = analyze_linked_origins(
            results, BASE_LINEAGE, links, BASE_TARGET
        )["reports"][0]
        # Equal sample/field path length, same dataset/field/rule class:
        # sample_id breaks the tie (a1 < a2); they are impacts.
        self.assertEqual(
            [item["sample_id"] for item in report["impactEvidence"]],
            ["a1", "a2"],
        )


class LinkedOriginTargetsOrderTest(unittest.TestCase):
    def test_reports_follow_targets_order(self):
        targets = [
            target("ads", "label", "a1"),
            target("dwd", "label", "s1"),
        ]
        reports = analyze_linked_origins(
            BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, targets
        )["reports"]
        self.assertEqual(
            [report["target"] for report in reports], targets
        )

    def test_duplicate_targets_produce_duplicate_reports(self):
        targets = [
            target("dwd", "label", "s1"),
            target("dwd", "label", "s1"),
        ]
        reports = analyze_linked_origins(
            BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, targets
        )["reports"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])

    def test_input_is_not_modified(self):
        results = [dict(record) for record in BASE_RESULTS]
        analyze_linked_origins(
            results, BASE_LINEAGE, BASE_LINKS, BASE_TARGET
        )
        self.assertEqual(results, BASE_RESULTS)
        for record in results:
            self.assertNotIn("sample_path", record)
            self.assertNotIn("path", record)


class LinkedOriginInputErrorTest(unittest.TestCase):
    def test_malformed_results(self):
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins("nope", BASE_LINEAGE, [], [])
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(
                [result("r1", "dwd", "label", "s1", violated="yes")],
                BASE_LINEAGE,
                [],
                [],
            )

    def test_malformed_lineage(self):
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins([], "nope", [], [])

    def test_malformed_targets(self):
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
            with self.assertRaises(LinkedOriginInputError):
                analyze_linked_origins(
                    BASE_RESULTS, BASE_LINEAGE, BASE_LINKS, bad_targets
                )

    def test_malformed_sample_links(self):
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(
                BASE_RESULTS, BASE_LINEAGE, "nope", BASE_TARGET
            )
        with self.assertRaises(LinkedOriginInputError):
            # Self link.
            analyze_linked_origins(
                BASE_RESULTS,
                BASE_LINEAGE,
                [link("dwd", "s1", "dwd", "s1")],
                BASE_TARGET,
            )
        with self.assertRaises(LinkedOriginInputError):
            # Duplicate (reversed) relationship.
            analyze_linked_origins(
                BASE_RESULTS,
                BASE_LINEAGE,
                [
                    link("dwd", "s1", "ods", "o1"),
                    link("ods", "o1", "dwd", "s1"),
                ],
                BASE_TARGET,
            )

    def test_conflicting_duplicate_results(self):
        results = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r1", "dwd", "label", "s1", value="b"),
        ]
        with self.assertRaises(LinkedOriginInputError):
            analyze_linked_origins(results, BASE_LINEAGE, [], [])


class LinkedOriginReferenceErrorTest(unittest.TestCase):
    def test_unknown_dataset(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(results, BASE_LINEAGE, [], BASE_TARGET)

    def test_unknown_field(self):
        results = [result("r1", "dwd", "ghost", "s1")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(results, BASE_LINEAGE, [], BASE_TARGET)

    def test_unknown_link_endpoint(self):
        bad_links = [
            link("dwd", "s1", "ods", "ghost")
        ]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                BASE_RESULTS, BASE_LINEAGE, bad_links, BASE_TARGET
            )

    def test_reference_error_precedes_target_error(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                results,
                BASE_LINEAGE,
                [],
                [target("dwd", "label", "zzz")],
            )

    def test_result_reference_error_precedes_link_error(self):
        # A bad result reference is reported before link endpoint errors.
        results = [result("r1", "ghost", "label", "s1")]
        bad_links = [link("dwd", "s1", "ods", "ghost")]
        with self.assertRaises(LinkedOriginReferenceError):
            analyze_linked_origins(
                results, BASE_LINEAGE, bad_links, BASE_TARGET
            )


class LinkedOriginTargetErrorTest(unittest.TestCase):
    def test_target_without_any_result(self):
        with self.assertRaises(LinkedOriginTargetError):
            analyze_linked_origins(
                BASE_RESULTS,
                BASE_LINEAGE,
                BASE_LINKS,
                [target("dwd", "label", "zzz")],
            )

    def test_target_with_only_passing_result(self):
        results = [
            result("r2", "dwd", "label", "s1", violated=False),
        ]
        with self.assertRaises(LinkedOriginTargetError):
            analyze_linked_origins(
                results, BASE_LINEAGE, [], BASE_TARGET
            )

    def test_target_with_undeclared_field(self):
        # Undeclared fields can never match a reference-checked result.
        with self.assertRaises(LinkedOriginTargetError):
            analyze_linked_origins(
                BASE_RESULTS,
                BASE_LINEAGE,
                BASE_LINKS,
                [target("dwd", "ghost", "s1")],
            )


if __name__ == "__main__":
    unittest.main()
