"""Tests for :func:`data_quality.correlate_sample_anomalies`."""

import unittest

from data_quality import (
    InvalidCorrelationGraphError,
    InvalidCorrelationInputError,
    UnknownCorrelationReferenceError,
    correlate_sample_anomalies,
)


def field(dataset, field_id):
    return {"dataset_id": dataset, "field_id": field_id}


def edge(source, target, edge_type):
    return {"source": source, "target": target, "type": edge_type}


def result(rule_id, dataset, field_id, sample_id, is_violation, value):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset,
        "field_id": field_id,
        "sample_id": sample_id,
        "is_violation": is_violation,
        "violating_value": value,
    }


GRAPH = {
    "nodes": [
        field("raw", "name"),
        field("ods", "name"),
        field("dwd", "label"),
        field("dwd", "id"),
        field("ads", "label"),
        field("ext", "code"),
    ],
    "edges": [
        edge(field("raw", "name"), field("ods", "name"), "downstream"),
        edge(field("ods", "name"), field("dwd", "label"), "downstream"),
        edge(field("dwd", "label"), field("ads", "label"), "downstream"),
    ],
}


class CorrelationSuccessTest(unittest.TestCase):
    def test_empty_results_and_empty_graph(self):
        self.assertEqual(
            correlate_sample_anomalies([], {"nodes": [], "edges": []}),
            {"events": []},
        )

    def test_no_violations_returns_empty_events(self):
        results = [
            result("r1", "dwd", "label", "s1", False, None),
            result("r2", "ods", "name", "s1", False, None),
        ]
        self.assertEqual(correlate_sample_anomalies(results, GRAPH), {"events": []})

    def test_no_placeholder_event_with_only_empty_fields(self):
        # A sample whose results all pass must not surface an event at all.
        results = [result("r1", "dwd", "label", "s1", False, None)]
        out = correlate_sample_anomalies(results, GRAPH)
        self.assertEqual(out, {"events": []})

    def test_single_rule_violation(self):
        results = [result("r1", "dwd", "label", "s1", True, "bad")]
        out = correlate_sample_anomalies(results, GRAPH)
        self.assertEqual(
            out,
            {
                "events": [
                    {
                        "sample_id": "s1",
                        "rule_ids": ["r1"],
                        "fields": [field("dwd", "label")],
                        "upstream_fields": [field("ods", "name")],
                        "downstream_fields": [field("ads", "label")],
                    }
                ]
            },
        )

    def test_multiple_rules_on_same_field_merge(self):
        results = [
            result("r1", "dwd", "label", "s1", True, "bad"),
            result("r2", "dwd", "label", "s1", True, 999),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule_ids"], ["r1", "r2"])
        self.assertEqual(events[0]["fields"], [field("dwd", "label")])

    def test_rules_on_adjacent_fields_merge(self):
        # raw.name -> ods.name: directly adjacent violations land in one
        # event; the event's direct neighbours exclude its own fields.
        results = [
            result("r-raw", "raw", "name", "s1", True, "x"),
            result("r-ods", "ods", "name", "s1", True, "y"),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["rule_ids"], ["r-ods", "r-raw"])
        self.assertEqual(
            event["fields"],
            [field("ods", "name"), field("raw", "name")],
        )
        self.assertEqual(event["upstream_fields"], [])
        self.assertEqual(
            event["downstream_fields"], [field("dwd", "label")]
        )

    def test_fields_two_edges_apart_with_clean_middle_do_not_merge(self):
        # raw.name and dwd.label are linked only through ods.name, which
        # has no violation; adjacency is one edge, so they stay separate.
        results = [
            result("r-raw", "raw", "name", "s1", True, "x"),
            result("r-dwd", "dwd", "label", "s1", True, "y"),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(
            [event["fields"] for event in events],
            [[field("dwd", "label")], [field("raw", "name")]],
        )

    def test_fields_in_same_dataset_without_edge_do_not_merge(self):
        # dwd.label and dwd.id share a dataset but have no lineage edge.
        results = [
            result("r-label", "dwd", "label", "s1", True, "x"),
            result("r-id", "dwd", "id", "s1", True, 9),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["fields"], [field("dwd", "id")])
        self.assertEqual(events[1]["fields"], [field("dwd", "label")])

    def test_disconnected_field_forms_own_event(self):
        results = [
            result("r-ext", "ext", "code", "s1", True, "z"),
            result("r-label", "dwd", "label", "s1", True, "x"),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["fields"], [field("dwd", "label")])
        self.assertEqual(
            events[0]["upstream_fields"], [field("ods", "name")]
        )
        self.assertEqual(
            events[0]["downstream_fields"], [field("ads", "label")]
        )
        self.assertEqual(events[1]["fields"], [field("ext", "code")])
        self.assertEqual(events[1]["upstream_fields"], [])
        self.assertEqual(events[1]["downstream_fields"], [])

    def test_upstream_typed_edge_orientation(self):
        graph = {
            "nodes": [field("a", "x"), field("b", "y")],
            "edges": [edge(field("b", "y"), field("a", "x"), "upstream")],
        }
        results = [result("r", "b", "y", "s", True, 1)]
        event = correlate_sample_anomalies(results, graph)["events"][0]
        self.assertEqual(event["upstream_fields"], [field("a", "x")])
        self.assertEqual(event["downstream_fields"], [])

    def test_events_sorted_by_sample_id(self):
        results = [
            result("r2", "dwd", "label", "s9", True, "a"),
            result("r1", "dwd", "label", "s1", True, "b"),
            result("r3", "dwd", "id", "s5", True, "c"),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertEqual(
            [event["sample_id"] for event in events], ["s1", "s5", "s9"]
        )

    def test_multiple_events_for_one_sample_are_all_emitted(self):
        results = [
            result("r-ext", "ext", "code", "s1", True, "z"),
            result("r-label", "dwd", "label", "s1", True, "x"),
            result("r-id", "dwd", "id", "s1", True, 9),
        ]
        events = correlate_sample_anomalies(results, GRAPH)["events"]
        self.assertTrue(all(event["sample_id"] == "s1" for event in events))
        self.assertEqual(len(events), 3)

    def test_duplicate_identical_result_kept_once(self):
        record = result("r1", "dwd", "label", "s1", True, "bad")
        events = correlate_sample_anomalies([record, dict(record)], GRAPH)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule_ids"], ["r1"])

    def test_passing_duplicate_does_not_create_event(self):
        record = result("r1", "dwd", "label", "s1", False, None)
        self.assertEqual(
            correlate_sample_anomalies([record, dict(record)], GRAPH),
            {"events": []},
        )

    def test_sets_are_deduplicated_and_sorted(self):
        # Two rules on each of two adjacent fields produce no duplicate
        # entries in fields / rule_ids / neighbour sets.
        results = [
            result("r-a1", "ods", "name", "s1", True, 1),
            result("r-a2", "ods", "name", "s1", True, 2),
            result("r-b1", "dwd", "label", "s1", True, 3),
            result("r-b2", "dwd", "label", "s1", True, 4),
        ]
        event = correlate_sample_anomalies(results, GRAPH)["events"][0]
        self.assertEqual(event["rule_ids"], ["r-a1", "r-a2", "r-b1", "r-b2"])
        self.assertEqual(
            event["fields"], [field("dwd", "label"), field("ods", "name")]
        )
        self.assertEqual(
            event["upstream_fields"], [field("raw", "name")]
        )
        self.assertEqual(
            event["downstream_fields"], [field("ads", "label")]
        )

    def test_deterministic_output_regardless_of_input_order(self):
        results = [
            result("r2", "ads", "label", "s1", True, "y"),
            result("r1", "raw", "name", "s1", True, "x"),
        ]
        shuffled = [results[1], results[0]]
        first = correlate_sample_anomalies(results, GRAPH)
        second = correlate_sample_anomalies(shuffled, GRAPH)
        self.assertEqual(first, second)

    def test_neighbours_inside_component_are_not_listed(self):
        # ods.name and dwd.label are both in the event; their connecting
        # edge must not show up in the neighbour sets.
        results = [
            result("r1", "ods", "name", "s1", True, "x"),
            result("r2", "dwd", "label", "s1", True, "y"),
        ]
        event = correlate_sample_anomalies(results, GRAPH)["events"][0]
        self.assertEqual(
            event["upstream_fields"], [field("raw", "name")]
        )
        self.assertEqual(
            event["downstream_fields"], [field("ads", "label")]
        )

    def test_empty_graph_with_no_violations(self):
        results = [result("r1", "dwd", "label", "s1", False, None)]
        # A passing result still references a field, which must be declared.
        graph = {"nodes": [field("dwd", "label")], "edges": []}
        self.assertEqual(
            correlate_sample_anomalies(results, graph), {"events": []}
        )

    def test_inputs_are_not_mutated(self):
        graph = {
            "nodes": [field("a", "x"), field("b", "y")],
            "edges": [edge(field("a", "x"), field("b", "y"), "downstream")],
        }
        graph_before = {"nodes": [dict(n) for n in graph["nodes"]],
                        "edges": [dict(e) for e in graph["edges"]]}
        records = [
            result("r1", "a", "x", "s1", True, 1),
            result("r2", "b", "y", "s1", True, 2),
        ]
        records_before = [dict(r) for r in records]
        correlate_sample_anomalies(records, graph)
        self.assertEqual(graph, graph_before)
        self.assertEqual(records, records_before)


class CorrelationInputErrorTest(unittest.TestCase):
    def assert_invalid(self, results, graph=GRAPH):
        with self.assertRaises(InvalidCorrelationInputError):
            correlate_sample_anomalies(results, graph)

    def test_results_not_a_list(self):
        self.assert_invalid({"rule_id": "r1"})

    def test_result_not_an_object(self):
        self.assert_invalid(["nope"])

    def test_missing_and_unknown_keys(self):
        self.assert_invalid([
            {"rule_id": "r", "dataset_id": "d", "field_id": "f",
             "sample_id": "s", "is_violation": True}
        ])
        self.assert_invalid([
            {"rule_id": "r", "dataset_id": "d", "field_id": "f",
             "sample_id": "s", "is_violation": True, "violating_value": None,
             "extra": 1}
        ])

    def test_empty_identifiers(self):
        kwargs = dict(dataset="d", field_id="f", sample_id="s",
                      is_violation=False, value=None)
        self.assert_invalid([result("", "d", "f", "s", False, None)])
        self.assert_invalid([result("r", "", "f", "s", False, None)])
        self.assert_invalid([result("r", "d", "", "s", False, None)])
        self.assert_invalid([result("r", "d", "f", "", False, None)])

    def test_non_string_identifiers(self):
        self.assert_invalid([result(1, "d", "f", "s", False, None)])
        self.assert_invalid([result("r", 3, "f", "s", False, None)])

    def test_is_violation_must_be_boolean(self):
        self.assert_invalid([result("r", "d", "label", "s", 1, None)],
                            {"nodes": [field("d", "label")], "edges": []})
        self.assert_invalid([result("r", "d", "label", "s", "true", None)],
                            {"nodes": [field("d", "label")], "edges": []})

    def test_conflicting_duplicate_raises_plain_value_error(self):
        records = [
            result("r", "d", "label", "s", True, 1),
            result("r", "d", "label", "s", True, 2),
        ]
        graph = {"nodes": [field("d", "label")], "edges": []}
        with self.assertRaises(ValueError):
            correlate_sample_anomalies(records, graph)

    def test_conflicting_duplicate_flag_raises_value_error(self):
        records = [
            result("r", "dwd", "label", "s", True, 1),
            result("r", "dwd", "label", "s", False, 1),
        ]
        with self.assertRaises(ValueError):
            correlate_sample_anomalies(records, GRAPH)

    def test_conflicting_duplicate_field_raises_value_error(self):
        records = [
            result("r", "dwd", "label", "s", True, 1),
            result("r", "dwd", "id", "s", True, 1),
        ]
        with self.assertRaises(ValueError):
            correlate_sample_anomalies(records, GRAPH)

    def test_conflicting_bool_vs_number_value_raises_value_error(self):
        # JSON true is not the value 1, even though Python equates them.
        records = [
            result("r", "dwd", "label", "s", True, True),
            result("r", "dwd", "label", "s", True, 1),
        ]
        graph = {"nodes": [field("dwd", "label")], "edges": []}
        with self.assertRaises(ValueError):
            correlate_sample_anomalies(records, graph)


class CorrelationGraphErrorTest(unittest.TestCase):
    def assert_graph_error(self, graph):
        with self.assertRaises(InvalidCorrelationGraphError):
            correlate_sample_anomalies([], graph)

    def test_graph_must_be_object_with_nodes_and_edges(self):
        with self.assertRaises(InvalidCorrelationGraphError):
            correlate_sample_anomalies([], [])
        with self.assertRaises(InvalidCorrelationGraphError):
            correlate_sample_anomalies([], {"edges": []})
        with self.assertRaises(InvalidCorrelationGraphError):
            correlate_sample_anomalies([], {"nodes": []})

    def test_nodes_must_be_list_of_field_objects(self):
        self.assert_graph_error({"nodes": "x", "edges": []})
        self.assert_graph_error({"nodes": ["x"], "edges": []})
        self.assert_graph_error(
            {"nodes": [{"dataset_id": "d"}], "edges": []}
        )
        self.assert_graph_error(
            {"nodes": [{"dataset_id": "d", "field_id": "f", "x": 1}],
             "edges": []}
        )
        self.assert_graph_error(
            {"nodes": [{"dataset_id": "", "field_id": "f"}], "edges": []}
        )

    def test_duplicate_node_rejected(self):
        self.assert_graph_error(
            {"nodes": [field("d", "f"), field("d", "f")], "edges": []}
        )

    def test_edges_must_be_list(self):
        self.assert_graph_error({"nodes": [], "edges": {}})

    def test_edge_keys(self):
        self.assert_graph_error(
            {"nodes": [field("d", "a"), field("d", "b")],
             "edges": [{"source": field("d", "a"), "target": field("d", "b")}]}
        )
        self.assert_graph_error(
            {"nodes": [field("d", "a"), field("d", "b")],
             "edges": [{"source": field("d", "a"), "target": field("d", "b"),
                        "type": "downstream", "extra": 1}]}
        )

    def test_illegal_edge_type(self):
        self.assert_graph_error(
            {"nodes": [field("d", "a"), field("d", "b")],
             "edges": [edge(field("d", "a"), field("d", "b"), "sideways")]}
        )

    def test_dangling_edge_endpoints(self):
        self.assert_graph_error(
            {"nodes": [field("d", "a")],
             "edges": [edge(field("d", "a"), field("d", "ghost"),
                            "downstream")]}
        )
        self.assert_graph_error(
            {"nodes": [field("d", "a")],
             "edges": [edge(field("x", "a"), field("d", "a"),
                            "downstream")]}
        )

    def test_contradictory_duplicate_edge(self):
        # Same endpoints, same declared direction but opposite flows.
        self.assert_graph_error(
            {"nodes": [field("d", "a"), field("d", "b")],
             "edges": [
                 edge(field("d", "a"), field("d", "b"), "downstream"),
                 edge(field("d", "a"), field("d", "b"), "upstream"),
             ]}
        )

    def test_consistent_duplicate_edge_allowed(self):
        graph = {
            "nodes": [field("d", "a"), field("d", "b")],
            "edges": [
                edge(field("d", "a"), field("d", "b"), "downstream"),
                edge(field("d", "b"), field("d", "a"), "upstream"),
                edge(field("d", "a"), field("d", "b"), "downstream"),
            ],
        }
        records = [result("r", "d", "a", "s", True, 1)]
        event = correlate_sample_anomalies(records, graph)["events"][0]
        self.assertEqual(event["downstream_fields"], [field("d", "b")])
        self.assertEqual(event["upstream_fields"], [])


class CorrelationUnknownReferenceTest(unittest.TestCase):
    def test_violation_on_unknown_field_raises_lookup_error(self):
        records = [result("r", "ghost", "f", "s", True, 1)]
        with self.assertRaises(UnknownCorrelationReferenceError):
            correlate_sample_anomalies(records, {"nodes": [], "edges": []})

    def test_passing_result_on_unknown_field_raises_lookup_error(self):
        records = [result("r", "d", "ghost", "s", False, None)]
        with self.assertRaises(UnknownCorrelationReferenceError):
            correlate_sample_anomalies(records, GRAPH)

    def test_error_is_lookup_error_not_value_error(self):
        self.assertTrue(issubclass(UnknownCorrelationReferenceError, LookupError))
        self.assertFalse(issubclass(UnknownCorrelationReferenceError, ValueError))


if __name__ == "__main__":
    unittest.main()
