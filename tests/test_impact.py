"""Tests for field-level data quality impact analysis."""

import unittest

from data_quality import ImpactInputError, analyze_field_impacts


def ref(dataset, field):
    return {"dataset": dataset, "field": field}


def edge(source_dataset, source_field, target_dataset, target_field):
    return {
        "sourceDataset": source_dataset,
        "sourceField": source_field,
        "targetDataset": target_dataset,
        "targetField": target_field,
    }


def rule(rule_id, status, fields, failed_sample_ids=()):
    return {
        "ruleId": rule_id,
        "status": status,
        "fields": fields,
        "failedSampleIds": list(failed_sample_ids),
    }


def sample(dataset, **field_values):
    return {"dataset": dataset, "fieldValues": field_values}


def payload(
    datasets=None,
    lineage_edges=None,
    validation_results=None,
    anomaly_samples=None,
    seed_fields=None,
):
    return {
        "datasets": datasets if datasets is not None else {},
        "lineageEdges": lineage_edges if lineage_edges is not None else [],
        "validationResults": (
            validation_results if validation_results is not None else []
        ),
        "anomalySamples": anomaly_samples if anomaly_samples is not None else {},
        "seedFields": seed_fields if seed_fields is not None else [],
    }


# ods.name -> dwd.label -> ads.label
BASE_DATASETS = {
    "ods": ["id", "name"],
    "dwd": ["id", "label"],
    "ads": ["label"],
}
BASE_EDGES = [
    edge("ods", "name", "dwd", "label"),
    edge("dwd", "label", "ads", "label"),
]


class ImpactDownstreamTest(unittest.TestCase):
    def test_empty_seed_fields_returns_empty_impacts(self):
        result = analyze_field_impacts(
            payload(datasets=BASE_DATASETS, lineage_edges=BASE_EDGES)
        )
        self.assertEqual(
            result,
            {"status": "ok", "impacts": [], "unresolvedReferences": []},
        )

    def test_collects_transitive_downstream_fields(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[ref("ods", "name")],
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["impacts"]), 1)
        impact = result["impacts"][0]
        self.assertEqual(impact["seed"], ref("ods", "name"))
        self.assertEqual(
            impact["downstreamFields"], ["ads.label", "dwd.label"]
        )
        self.assertEqual(impact["affectedRules"], [])
        self.assertEqual(impact["anomalySamples"], [])

    def test_seed_without_edges_has_empty_downstream(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[ref("dwd", "id")],
            )
        )
        impact = result["impacts"][0]
        self.assertEqual(impact["downstreamFields"], [])
        self.assertEqual(impact["paths"], [])

    def test_unknown_seed_is_treated_as_empty_downstream(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[ref("ods", "missing"), ref("unknown", "name")],
            )
        )
        self.assertEqual(len(result["impacts"]), 2)
        for impact in result["impacts"]:
            self.assertEqual(impact["downstreamFields"], [])
            self.assertEqual(impact["affectedRules"], [])
            self.assertEqual(impact["anomalySamples"], [])
            self.assertEqual(impact["paths"], [])

    def test_impacts_follow_seed_order_and_duplicates_are_kept(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[
                    ref("dwd", "label"),
                    ref("ods", "name"),
                    ref("dwd", "label"),
                ],
            )
        )
        self.assertEqual(
            [impact["seed"] for impact in result["impacts"]],
            [ref("dwd", "label"), ref("ods", "name"), ref("dwd", "label")],
        )
        self.assertEqual(
            result["impacts"][0], result["impacts"][2]
        )

    def test_self_loop_makes_seed_its_own_downstream(self):
        result = analyze_field_impacts(
            payload(
                datasets={"ods": ["name"]},
                lineage_edges=[edge("ods", "name", "ods", "name")],
                seed_fields=[ref("ods", "name")],
            )
        )
        impact = result["impacts"][0]
        self.assertEqual(impact["downstreamFields"], ["ods.name"])
        self.assertEqual(
            impact["paths"],
            [
                {
                    "field": "ods.name",
                    "path": [ref("ods", "name"), ref("ods", "name")],
                }
            ],
        )

    def test_cycle_back_to_seed_terminates_and_counts(self):
        # ods.name -> dwd.label -> ods.name
        result = analyze_field_impacts(
            payload(
                datasets={"ods": ["name"], "dwd": ["label"]},
                lineage_edges=[
                    edge("ods", "name", "dwd", "label"),
                    edge("dwd", "label", "ods", "name"),
                ],
                seed_fields=[ref("ods", "name")],
            )
        )
        impact = result["impacts"][0]
        self.assertEqual(
            impact["downstreamFields"], ["dwd.label", "ods.name"]
        )
        self.assertEqual(
            impact["paths"],
            [
                {
                    "field": "dwd.label",
                    "path": [ref("ods", "name"), ref("dwd", "label")],
                },
                {
                    "field": "ods.name",
                    "path": [
                        ref("ods", "name"),
                        ref("dwd", "label"),
                        ref("ods", "name"),
                    ],
                },
            ],
        )


class ImpactPathsTest(unittest.TestCase):
    def test_paths_hold_shortest_reference_sequences(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[ref("ods", "name")],
            )
        )
        self.assertEqual(
            result["impacts"][0]["paths"],
            [
                {
                    "field": "ads.label",
                    "path": [
                        ref("ods", "name"),
                        ref("dwd", "label"),
                        ref("ads", "label"),
                    ],
                },
                {
                    "field": "dwd.label",
                    "path": [ref("ods", "name"), ref("dwd", "label")],
                },
            ],
        )

    def test_equal_length_paths_pick_lexicographically_smallest(self):
        # Two shortest routes to ads.label: via dwd.a or via dwd.b.
        result = analyze_field_impacts(
            payload(
                datasets={"ods": ["name"], "dwd": ["a", "b"], "ads": ["label"]},
                lineage_edges=[
                    edge("ods", "name", "dwd", "b"),
                    edge("ods", "name", "dwd", "a"),
                    edge("dwd", "a", "ads", "label"),
                    edge("dwd", "b", "ads", "label"),
                ],
                seed_fields=[ref("ods", "name")],
            )
        )
        paths = result["impacts"][0]["paths"]
        self.assertEqual(
            [entry["field"] for entry in paths],
            ["ads.label", "dwd.a", "dwd.b"],
        )
        self.assertEqual(
            paths[0]["path"],
            [ref("ods", "name"), ref("dwd", "a"), ref("ads", "label")],
        )


class ImpactRulesAndSamplesTest(unittest.TestCase):
    def test_affected_rules_exclude_passed_and_unrelated(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                validation_results=[
                    rule("r-failed", "failed", [ref("ads", "label")], ["s1"]),
                    rule("r-skipped", "skipped", [ref("dwd", "label")], ["s2"]),
                    rule("r-passed", "passed", [ref("ads", "label")], ["s3"]),
                    rule("r-unrelated", "failed", [ref("ods", "id")], ["s4"]),
                ],
                anomaly_samples={
                    "s1": sample("ads", label="x"),
                    "s2": sample("dwd", label="y"),
                },
                seed_fields=[ref("ods", "name")],
            )
        )
        impact = result["impacts"][0]
        self.assertEqual(impact["affectedRules"], ["r-failed", "r-skipped"])
        self.assertEqual(impact["anomalySamples"], ["s1", "s2"])

    def test_anomaly_samples_keep_only_locatable_ids(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                validation_results=[
                    rule("r1", "failed", [ref("ads", "label")], ["s2", "s9"]),
                ],
                anomaly_samples={"s2": sample("ads", label=None)},
                seed_fields=[ref("dwd", "label")],
            )
        )
        self.assertEqual(result["impacts"][0]["anomalySamples"], ["s2"])

    def test_rules_touching_only_the_seed_are_not_affected(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                validation_results=[
                    rule("r-seed", "failed", [ref("ods", "name")], ["s1"]),
                ],
                anomaly_samples={"s1": sample("ods", name="n")},
                seed_fields=[ref("ods", "name")],
            )
        )
        self.assertEqual(result["impacts"][0]["affectedRules"], [])


class ImpactUnresolvedTest(unittest.TestCase):
    def test_dangling_edges_are_reported_not_fatal(self):
        result = analyze_field_impacts(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=[
                    edge("ods", "name", "dwd", "label"),
                    edge("dwd", "label", "ads", "label"),
                    edge("dwd", "label", "ads", "ghost"),
                    edge("ghost", "x", "dwd", "label"),
                ],
                seed_fields=[ref("ods", "name")],
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["impacts"][0]["downstreamFields"],
            ["ads.label", "dwd.label"],
        )
        self.assertEqual(
            result["unresolvedReferences"],
            [
                edge("dwd", "label", "ads", "ghost"),
                edge("ghost", "x", "dwd", "label"),
            ],
        )

    def test_unresolved_references_are_sorted_and_deduplicated(self):
        result = analyze_field_impacts(
            payload(
                datasets={"ods": ["name"]},
                lineage_edges=[
                    edge("b", "y", "b", "z"),
                    edge("a", "x", "b", "y"),
                    edge("a", "x", "b", "y"),
                    edge("a", "w", "a", "x"),
                ],
            )
        )
        self.assertEqual(result["impacts"], [])
        self.assertEqual(
            result["unresolvedReferences"],
            [
                edge("a", "w", "a", "x"),
                edge("a", "x", "b", "y"),
                edge("b", "y", "b", "z"),
            ],
        )


class ImpactInputErrorTest(unittest.TestCase):
    def assert_impact_error(self, value):
        with self.assertRaises(ImpactInputError) as ctx:
            analyze_field_impacts(value)
        self.assertIsInstance(ctx.exception, ValueError)

    def test_payload_must_be_an_object(self):
        for bad in (None, [], "x", 1, True):
            self.assert_impact_error(bad)

    def test_payload_missing_and_extra_keys(self):
        self.assert_impact_error({})
        incomplete = payload()
        del incomplete["seedFields"]
        self.assert_impact_error(incomplete)
        extra = payload()
        extra["other"] = 1
        self.assert_impact_error(extra)

    def test_datasets_structure(self):
        self.assert_impact_error(payload(datasets=[]))
        self.assert_impact_error(payload(datasets={"": ["a"]}))
        self.assert_impact_error(payload(datasets={"ods": "name"}))
        self.assert_impact_error(payload(datasets={"ods": [""]}))
        self.assert_impact_error(payload(datasets={"ods": [1]}))
        self.assert_impact_error(payload(datasets={"ods": [True]}))

    def test_lineage_edges_structure(self):
        self.assert_impact_error(payload(lineage_edges={}))
        self.assert_impact_error(payload(lineage_edges=["x"]))
        self.assert_impact_error(payload(lineage_edges=[{"sourceDataset": "a"}]))
        broken = edge("a", "b", "c", "d")
        broken["extra"] = 1
        self.assert_impact_error(payload(lineage_edges=[broken]))
        self.assert_impact_error(
            payload(lineage_edges=[edge("", "b", "c", "d")])
        )
        self.assert_impact_error(
            payload(lineage_edges=[edge("a", "b", "c", None)])
        )

    def test_validation_results_structure(self):
        self.assert_impact_error(payload(validation_results={}))
        self.assert_impact_error(payload(validation_results=[[]]))
        self.assert_impact_error(
            payload(validation_results=[rule("", "failed", [])])
        )
        for bad_status in ("unknown", "", None, 1, True):
            self.assert_impact_error(
                payload(validation_results=[rule("r", bad_status, [])])
            )
        for ok_status in ("passed", "failed", "skipped"):
            analyze_field_impacts(
                payload(validation_results=[rule("r", ok_status, [])])
            )
        self.assert_impact_error(
            payload(validation_results=[rule("r", "failed", {})])
        )
        self.assert_impact_error(
            payload(validation_results=[rule("r", "failed", ["x"])])
        )
        self.assert_impact_error(
            payload(validation_results=[rule("r", "failed", [ref("a", "")])])
        )
        with_extra = rule("r", "failed", [])
        with_extra["extra"] = 1
        self.assert_impact_error(payload(validation_results=[with_extra]))
        self.assert_impact_error(
            payload(
                validation_results=[
                    {
                        "ruleId": "r",
                        "status": "failed",
                        "fields": [],
                        "failedSampleIds": [""],
                    }
                ]
            )
        )

    def test_anomaly_samples_structure(self):
        self.assert_impact_error(payload(anomaly_samples=[]))
        self.assert_impact_error(payload(anomaly_samples={"": sample("ods")}))
        self.assert_impact_error(payload(anomaly_samples={"s1": []}))
        self.assert_impact_error(payload(anomaly_samples={"s1": {"dataset": "ods"}}))
        self.assert_impact_error(
            payload(anomaly_samples={"s1": {"dataset": "", "fieldValues": {}}})
        )
        self.assert_impact_error(
            payload(
                anomaly_samples={"s1": {"dataset": "ods", "fieldValues": []}}
            )
        )
        with_extra = sample("ods")
        with_extra["extra"] = 1
        self.assert_impact_error(payload(anomaly_samples={"s1": with_extra}))

    def test_seed_fields_structure(self):
        self.assert_impact_error(payload(seed_fields={}))
        self.assert_impact_error(payload(seed_fields=["ods.name"]))
        self.assert_impact_error(payload(seed_fields=[{"dataset": "ods"}]))
        self.assert_impact_error(payload(seed_fields=[ref("ods", "")]))
        self.assert_impact_error(payload(seed_fields=[ref(1, "name")]))
        with_extra = ref("ods", "name")
        with_extra["extra"] = 1
        self.assert_impact_error(payload(seed_fields=[with_extra]))

    def test_error_is_not_partial(self):
        # A structural problem anywhere invalidates the whole analysis.
        self.assert_impact_error(
            payload(
                datasets=BASE_DATASETS,
                lineage_edges=BASE_EDGES,
                seed_fields=[ref("ods", "name"), "not-a-reference"],
            )
        )


if __name__ == "__main__":
    unittest.main()
