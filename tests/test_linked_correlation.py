"""Tests for correlate_linked_violations cross-dataset sample correlation."""

import unittest

from data_quality.correlation import (
    InvalidCorrelationInputError,
    InvalidLinkedCorrelationInputError,
    UnknownCorrelationReferenceError,
    UnknownLinkedCorrelationReferenceError,
    correlate_linked_violations,
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


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": {"dataset_id": left_dataset, "sample_id": left_sample},
        "right": {"dataset_id": right_dataset, "sample_id": right_sample},
    }


def sample_ref(dataset_id, sample_id):
    return {"dataset_id": dataset_id, "sample_id": sample_id}


LINEAGE = {
    "datasets": ["ods", "dwd"],
    "fields": {"ods": ["name"], "dwd": ["id", "label"]},
    "edges": [edge("dwd", "label", "ods", "name", "upstream")],
}


class LinkedCorrelationTest(unittest.TestCase):
    def test_linked_samples_merge_into_one_event(self):
        results = [
            result("r2", "dwd", "label", "s-1", value="bad"),
            result("r1", "ods", "name", "s-2", value="x"),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "ods", "s-2")]
        )
        self.assertEqual(
            out,
            {
                "events": [
                    {
                        "sample_refs": [
                            sample_ref("dwd", "s-1"),
                            sample_ref("ods", "s-2"),
                        ],
                        "rules": ["r1", "r2"],
                        "fields": [
                            endpoint("dwd", "label"),
                            endpoint("ods", "name"),
                        ],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    }
                ]
            },
        )

    def test_transitive_closure_groups_indirectly_linked_samples(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "dwd", "label", "s-2"),
            result("r3", "ods", "name", "s-3"),
        ]
        links = [
            link("dwd", "s-1", "dwd", "s-2"),
            link("dwd", "s-2", "ods", "s-3"),
        ]
        out = correlate_linked_violations(results, LINEAGE, links)
        self.assertEqual(len(out["events"]), 1)
        event = out["events"][0]
        self.assertEqual(
            event["sample_refs"],
            [
                sample_ref("dwd", "s-1"),
                sample_ref("dwd", "s-2"),
                sample_ref("ods", "s-3"),
            ],
        )
        self.assertEqual(event["rules"], ["r1", "r2", "r3"])

    def test_non_adjacent_fields_split_into_separate_events(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "dwd", "id", "s-2"),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "dwd", "s-2")]
        )
        self.assertEqual(len(out["events"]), 2)
        self.assertEqual(out["events"][0]["fields"], [endpoint("dwd", "label")])
        self.assertEqual(out["events"][0]["sample_refs"], [sample_ref("dwd", "s-1")])
        self.assertEqual(out["events"][1]["fields"], [endpoint("dwd", "id")])
        self.assertEqual(out["events"][1]["sample_refs"], [sample_ref("dwd", "s-2")])

    def test_upstream_downstream_neighbours_outside_group(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "dwd", "label", "s-2"),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "dwd", "s-2")]
        )
        self.assertEqual(len(out["events"]), 1)
        event = out["events"][0]
        self.assertEqual(event["fields"], [endpoint("dwd", "label")])
        self.assertEqual(event["upstream_fields"], [endpoint("ods", "name")])
        self.assertEqual(event["downstream_fields"], [])

    def test_rules_deduplicated_across_samples(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r1", "dwd", "label", "s-2"),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "dwd", "s-2")]
        )
        self.assertEqual(out["events"][0]["rules"], ["r1"])

    def test_passing_only_component_produces_no_event(self):
        results = [
            result("r1", "dwd", "label", "s-1", violated=False),
            result("r2", "ods", "name", "s-2", violated=False),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "ods", "s-2")]
        )
        self.assertEqual(out, {"events": []})

    def test_passing_samples_are_excluded_from_event(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "ods", "name", "s-2", violated=False),
        ]
        out = correlate_linked_violations(
            results, LINEAGE, [link("dwd", "s-1", "ods", "s-2")]
        )
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(
            out["events"][0]["sample_refs"], [sample_ref("dwd", "s-1")]
        )

    def test_empty_links_and_no_violations_yield_empty_events(self):
        results = [result("r1", "dwd", "label", "s-1", violated=False)]
        self.assertEqual(
            correlate_linked_violations(results, LINEAGE, []), {"events": []}
        )
        violated = [result("r1", "dwd", "label", "s-1")]
        self.assertEqual(
            correlate_linked_violations(violated, LINEAGE, []), {"events": []}
        )

    def test_unlinked_samples_produce_no_events(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "ods", "name", "s-2"),
        ]
        out = correlate_linked_violations(results, LINEAGE, [])
        self.assertEqual(out, {"events": []})


class LinkedCorrelationInputValidationTest(unittest.TestCase):
    def test_links_must_be_a_list(self):
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations([], LINEAGE, {})

    def test_link_must_be_an_object_with_exact_keys(self):
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations([], LINEAGE, ["x"])
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations([], LINEAGE, [{"left": {}}])
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations(
                [], LINEAGE, [{"left": {}, "right": {}, "extra": 1}]
            )

    def test_endpoint_must_have_exact_keys_and_non_empty_ids(self):
        results = [result("r1", "dwd", "label", "s-1")]
        bad_endpoints = [
            {"dataset_id": "dwd"},
            {"dataset_id": "dwd", "sample_id": "s-1", "value": 1},
            {"dataset_id": "", "sample_id": "s-1"},
            {"dataset_id": "dwd", "sample_id": ""},
            {"dataset_id": "dwd", "sample_id": 7},
        ]
        for bad in bad_endpoints:
            with self.assertRaises(InvalidLinkedCorrelationInputError, msg=bad):
                correlate_linked_violations(
                    results,
                    LINEAGE,
                    [{"left": bad, "right": sample_ref("dwd", "s-1")}],
                )

    def test_self_link_rejected(self):
        results = [result("r1", "dwd", "label", "s-1")]
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations(
                results, LINEAGE, [link("dwd", "s-1", "dwd", "s-1")]
            )

    def test_duplicate_link_rejected_in_either_direction(self):
        results = [
            result("r1", "dwd", "label", "s-1"),
            result("r2", "ods", "name", "s-2"),
        ]
        duplicates = [
            [link("dwd", "s-1", "ods", "s-2"), link("dwd", "s-1", "ods", "s-2")],
            [link("dwd", "s-1", "ods", "s-2"), link("ods", "s-2", "dwd", "s-1")],
        ]
        for links in duplicates:
            with self.assertRaises(InvalidLinkedCorrelationInputError):
                correlate_linked_violations(results, LINEAGE, links)

    def test_unknown_endpoint_rejected(self):
        results = [result("r1", "dwd", "label", "s-1")]
        with self.assertRaises(UnknownLinkedCorrelationReferenceError):
            correlate_linked_violations(
                results, LINEAGE, [link("dwd", "s-1", "dwd", "s-9")]
            )
        with self.assertRaises(UnknownLinkedCorrelationReferenceError):
            correlate_linked_violations(
                results, LINEAGE, [link("dwd", "s-1", "ods", "s-1")]
            )

    def test_results_and_lineage_keep_correlate_semantics(self):
        with self.assertRaises(InvalidCorrelationInputError):
            correlate_linked_violations([{"rule_id": "r1"}], LINEAGE, [])
        with self.assertRaises(UnknownCorrelationReferenceError):
            correlate_linked_violations(
                [result("r1", "ods", "ghost", "s-1")], LINEAGE, []
            )

    def test_error_codes(self):
        self.assertEqual(
            InvalidLinkedCorrelationInputError.code,
            "INVALID_LINKED_CORRELATION_INPUT",
        )
        self.assertEqual(
            UnknownLinkedCorrelationReferenceError.code,
            "UNKNOWN_LINKED_CORRELATION_REFERENCE",
        )
        self.assertTrue(issubclass(InvalidLinkedCorrelationInputError, ValueError))
        self.assertTrue(
            issubclass(UnknownLinkedCorrelationReferenceError, LookupError)
        )


if __name__ == "__main__":
    unittest.main()
