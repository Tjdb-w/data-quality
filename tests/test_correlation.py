"""Tests for cross-rule correlation of anomalous samples."""

import copy
import unittest

from data_quality import (
    InvalidCorrelationInputError,
    UnknownCorrelationReferenceError,
    correlate_violations,
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


# ods.name -> dwd.label -> ads.label, plus dwd.id with no edges.
BASE_LINEAGE = lineage(
    ["ods", "dwd", "ads"],
    {"ods": ["id", "name"], "dwd": ["id", "label"], "ads": ["label"]},
    [
        edge("dwd", "label", "ods", "name", "upstream"),
        edge("dwd", "label", "ads", "label", "downstream"),
    ],
)


class CorrelateEmptyTest(unittest.TestCase):
    def test_empty_results_and_empty_graph(self):
        empty_graph = lineage([], {}, [])
        self.assertEqual(correlate_violations([], empty_graph), {"events": []})

    def test_empty_results_with_graph(self):
        self.assertEqual(
            correlate_violations([], BASE_LINEAGE), {"events": []}
        )

    def test_no_violations_produces_no_placeholder_events(self):
        results = [
            result("r1", "dwd", "label", "s1", violated=False, value="ok"),
            result("r2", "dwd", "id", "s2", violated=False, value=1),
        ]
        self.assertEqual(
            correlate_violations(results, BASE_LINEAGE), {"events": []}
        )


class CorrelateGroupingTest(unittest.TestCase):
    def test_single_rule_violation(self):
        results = [result("r1", "dwd", "label", "s1", value="bad")]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["sample_id"], "s1")
        self.assertEqual(event["rules"], ["r1"])
        self.assertEqual(event["fields"], [endpoint("dwd", "label")])
        self.assertEqual(event["upstream_fields"], [endpoint("ods", "name")])
        self.assertEqual(event["downstream_fields"], [endpoint("ads", "label")])

    def test_same_field_multiple_rules_form_one_event(self):
        results = [
            result("r2", "dwd", "label", "s1", value="bad"),
            result("r1", "dwd", "label", "s1", value="bad"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rules"], ["r1", "r2"])
        self.assertEqual(events[0]["fields"], [endpoint("dwd", "label")])

    def test_adjacent_fields_merge_into_one_event(self):
        results = [
            result("r1", "ods", "name", "s1", value="x"),
            result("r2", "dwd", "label", "s1", value="y"),
            result("r3", "ads", "label", "s1", value="z"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["rules"], ["r1", "r2", "r3"])
        self.assertEqual(
            event["fields"],
            [
                endpoint("ads", "label"),
                endpoint("dwd", "label"),
                endpoint("ods", "name"),
            ],
        )
        # All neighbours are affected, so the context sets are empty.
        self.assertEqual(event["upstream_fields"], [])
        self.assertEqual(event["downstream_fields"], [])

    def test_transitive_adjacency_merges(self):
        # ods.name and ads.label are only connected through dwd.label.
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "s1"),
            result("r3", "ads", "label", "s1"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)

    def test_same_dataset_without_edge_does_not_merge(self):
        results = [
            result("r1", "dwd", "id", "s1", value=1),
            result("r2", "dwd", "label", "s1", value="bad"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(event["sample_id"], "s1")
        self.assertEqual(events[0]["fields"], [endpoint("dwd", "id")])
        self.assertEqual(events[0]["rules"], ["r1"])
        self.assertEqual(events[1]["fields"], [endpoint("dwd", "label")])
        self.assertEqual(events[1]["rules"], ["r2"])

    def test_events_sorted_by_sample_id(self):
        results = [
            result("r1", "dwd", "label", "s2"),
            result("r2", "dwd", "label", "s1"),
            result("r1", "dwd", "id", "s1"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(
            [event["sample_id"] for event in events], ["s1", "s1", "s2"]
        )

    def test_non_violated_results_do_not_join_events(self):
        results = [
            result("r1", "dwd", "label", "s1", violated=True, value="bad"),
            result("r2", "dwd", "id", "s1", violated=False, value=7),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rules"], ["r1"])
        self.assertEqual(events[0]["fields"], [endpoint("dwd", "label")])

    def test_context_sets_exclude_affected_fields(self):
        # dwd.id has no edges; dwd.label violated together with ods.name.
        results = [
            result("r1", "dwd", "label", "s1"),
            result("r2", "ods", "name", "s1"),
        ]
        events = correlate_violations(results, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        # ods.name is affected, so it is not repeated as upstream context.
        self.assertEqual(event["upstream_fields"], [])
        self.assertEqual(event["downstream_fields"], [endpoint("ads", "label")])

    def test_deterministic_output(self):
        results = [
            result("r2", "dwd", "label", "s2", value="bad"),
            result("r1", "ods", "name", "s1", value="x"),
            result("r3", "dwd", "label", "s1", value="y"),
        ]
        first = correlate_violations(results, BASE_LINEAGE)
        second = correlate_violations(
            list(reversed(results)), BASE_LINEAGE
        )
        self.assertEqual(first, second)

    def test_input_is_not_mutated(self):
        results = [result("r1", "dwd", "label", "s1", value=[1, 2])]
        graph = copy.deepcopy(BASE_LINEAGE)
        results_snapshot = copy.deepcopy(results)
        graph_snapshot = copy.deepcopy(graph)
        correlate_violations(results, graph)
        self.assertEqual(results, results_snapshot)
        self.assertEqual(graph, graph_snapshot)


class CorrelateDuplicateResultTest(unittest.TestCase):
    def test_identical_duplicate_is_kept_once(self):
        record = result("r1", "dwd", "label", "s1", value={"a": [1, 2]})
        duplicate = result("r1", "dwd", "label", "s1", value={"a": [1, 2]})
        events = correlate_violations([record, duplicate], BASE_LINEAGE)[
            "events"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rules"], ["r1"])

    def test_conflicting_value_raises(self):
        records = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r1", "dwd", "label", "s1", value="b"),
        ]
        with self.assertRaises(ValueError):
            correlate_violations(records, BASE_LINEAGE)

    def test_conflicting_field_raises(self):
        records = [
            result("r1", "dwd", "label", "s1"),
            result("r1", "dwd", "id", "s1"),
        ]
        with self.assertRaises(ValueError):
            correlate_violations(records, BASE_LINEAGE)

    def test_conflicting_violated_flag_raises(self):
        records = [
            result("r1", "dwd", "label", "s1", violated=True),
            result("r1", "dwd", "label", "s1", violated=False),
        ]
        with self.assertRaises(ValueError):
            correlate_violations(records, BASE_LINEAGE)

    def test_same_rule_on_different_samples_is_not_a_duplicate(self):
        records = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r1", "dwd", "id", "s2", value="b"),
        ]
        events = correlate_violations(records, BASE_LINEAGE)["events"]
        self.assertEqual(len(events), 2)


class CorrelateResultValidationTest(unittest.TestCase):
    def assert_invalid(self, results, graph=BASE_LINEAGE):
        with self.assertRaises(ValueError):
            correlate_violations(results, graph)

    def test_results_must_be_a_list(self):
        self.assert_invalid(None)
        self.assert_invalid({})

    def test_result_must_be_an_object(self):
        self.assert_invalid(["nope"])

    def test_result_keys_are_strict(self):
        record = result("r1", "dwd", "label", "s1")
        del record["value"]
        self.assert_invalid([record])

        record = result("r1", "dwd", "label", "s1")
        record["timestamp"] = "2026-10-04"
        self.assert_invalid([record])

    def test_empty_identifiers_raise(self):
        for key in ("rule_id", "dataset_id", "field_id", "sample_id"):
            record = result("r1", "dwd", "label", "s1")
            record[key] = ""
            self.assert_invalid([record])
            record[key] = None
            self.assert_invalid([record])
            record[key] = 7
            self.assert_invalid([record])

    def test_violated_must_be_boolean(self):
        record = result("r1", "dwd", "label", "s1")
        record["violated"] = "true"
        self.assert_invalid([record])


class CorrelateLineageValidationTest(unittest.TestCase):
    ONE_RESULT = [result("r1", "dwd", "label", "s1")]

    def assert_invalid(self, graph):
        with self.assertRaises(ValueError):
            correlate_violations(self.ONE_RESULT, graph)

    def test_lineage_must_be_an_object_with_exact_keys(self):
        self.assert_invalid(None)
        self.assert_invalid([])
        graph = lineage(["dwd"], {"dwd": ["label"]}, [])
        del graph["edges"]
        self.assert_invalid(graph)
        graph = lineage(["dwd"], {"dwd": ["label"]}, [])
        graph["extra"] = 1
        self.assert_invalid(graph)

    def test_dataset_ids_must_be_non_empty_and_distinct(self):
        self.assert_invalid(lineage([""], {}, []))
        self.assert_invalid(lineage(["dwd", "dwd"], {"dwd": ["label"]}, []))

    def test_fields_must_target_declared_datasets(self):
        self.assert_invalid(lineage(["dwd"], {"ghost": ["label"]}, []))

    def test_field_ids_must_be_non_empty_and_distinct(self):
        self.assert_invalid(lineage(["dwd"], {"dwd": [""]}, []))
        self.assert_invalid(lineage(["dwd"], {"dwd": ["a", "a"]}, []))

    def test_dangling_edge_raises(self):
        graph = lineage(
            ["dwd"], {"dwd": ["label"]},
            [edge("dwd", "label", "dwd", "ghost", "upstream")],
        )
        self.assert_invalid(graph)

    def test_illegal_edge_type_raises(self):
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["label"]},
            [edge("dwd", "label", "ods", "name", "sideways")],
        )
        self.assert_invalid(graph)

    def test_contradictory_edges_raise(self):
        # Same pair asserted upstream and downstream.
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["label"]},
            [
                edge("dwd", "label", "ods", "name", "upstream"),
                edge("dwd", "label", "ods", "name", "downstream"),
            ],
        )
        self.assert_invalid(graph)

        # Opposite directions asserted by two upstream edges.
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["label"]},
            [
                edge("dwd", "label", "ods", "name", "upstream"),
                edge("ods", "name", "dwd", "label", "upstream"),
            ],
        )
        self.assert_invalid(graph)

    def test_equivalent_duplicate_edges_are_kept_once(self):
        graph = lineage(
            ["ods", "dwd"],
            {"ods": ["name"], "dwd": ["label"]},
            [
                edge("dwd", "label", "ods", "name", "upstream"),
                edge("dwd", "label", "ods", "name", "upstream"),
                # A downstream edge from the other endpoint restates it.
                edge("ods", "name", "dwd", "label", "downstream"),
            ],
        )
        events = correlate_violations(self.ONE_RESULT, graph)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["upstream_fields"], [endpoint("ods", "name")]
        )


class CorrelateReferenceTest(unittest.TestCase):
    def test_unknown_dataset_raises_lookup_error(self):
        results = [result("r1", "ghost", "label", "s1")]
        with self.assertRaises(LookupError):
            correlate_violations(results, BASE_LINEAGE)

    def test_unknown_field_raises_lookup_error(self):
        results = [result("r1", "dwd", "ghost", "s1")]
        with self.assertRaises(LookupError):
            correlate_violations(results, BASE_LINEAGE)

    def test_lookup_error_is_not_value_error(self):
        results = [result("r1", "dwd", "ghost", "s1")]
        with self.assertRaises(UnknownCorrelationReferenceError):
            correlate_violations(results, BASE_LINEAGE)
        self.assertFalse(
            issubclass(UnknownCorrelationReferenceError, ValueError)
        )
        self.assertTrue(issubclass(InvalidCorrelationInputError, ValueError))

    def test_non_violated_results_are_reference_checked_too(self):
        results = [result("r1", "dwd", "ghost", "s1", violated=False)]
        with self.assertRaises(LookupError):
            correlate_violations(results, BASE_LINEAGE)


if __name__ == "__main__":
    unittest.main()
