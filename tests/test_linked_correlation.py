"""Tests for cross-dataset correlation over linked samples."""

import copy
import unittest

from data_quality import (
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


def field_ref(dataset, field):
    return {"dataset": dataset, "field": field}


def sample_ref(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": field_ref(src_dataset, src_field),
        "target": field_ref(dst_dataset, dst_field),
        "type": edge_type,
    }


def lineage(datasets, fields, edges):
    return {"datasets": datasets, "fields": fields, "edges": edges}


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_ref(left_dataset, left_sample),
        "right": sample_ref(right_dataset, right_sample),
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


class LinkedCorrelateEmptyTest(unittest.TestCase):
    def test_empty_results_empty_links(self):
        self.assertEqual(
            correlate_linked_violations([], lineage([], {}, []), []),
            {"events": []},
        )

    def test_empty_links_is_valid(self):
        results = [result("r1", "dwd", "label", "s1")]
        events = correlate_linked_violations(results, BASE_LINEAGE, [])[
            "events"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sample_refs"], [sample_ref("dwd", "s1")])

    def test_no_violations_produces_no_events(self):
        results = [
            result("r1", "dwd", "label", "s1", violated=False),
            result("r2", "ods", "name", "s2", violated=False, value="ok"),
        ]
        links = [link("dwd", "s1", "ods", "s2")]
        self.assertEqual(
            correlate_linked_violations(results, BASE_LINEAGE, links),
            {"events": []},
        )

    def test_passing_only_component_emits_nothing_even_when_linked(self):
        results = [
            result("r1", "dwd", "label", "s1", violated=False),
            result("r2", "dwd", "id", "s2", violated=False),
        ]
        self.assertEqual(
            correlate_linked_violations(
                results, BASE_LINEAGE, [link("dwd", "s1", "dwd", "s2")]
            ),
            {"events": []},
        )


class LinkedCorrelateClosureTest(unittest.TestCase):
    def test_link_merges_samples_across_datasets(self):
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "t1"),
        ]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, [link("ods", "s1", "dwd", "t1")]
        )["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(
            event["sample_refs"],
            [sample_ref("dwd", "t1"), sample_ref("ods", "s1")],
        )
        self.assertEqual(event["rules"], ["r1", "r2"])
        self.assertEqual(
            event["fields"],
            [field_ref("dwd", "label"), field_ref("ods", "name")],
        )
        # Both affected fields are lineage-adjacent, so no context fields.
        self.assertEqual(event["upstream_fields"], [])
        self.assertEqual(event["downstream_fields"], [
            field_ref("ads", "label")
        ])

    def test_transitive_closure_merges_chain(self):
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "t1"),
            result("r3", "ads", "label", "u1"),
        ]
        links = [
            link("ods", "s1", "dwd", "t1"),
            link("dwd", "t1", "ads", "u1"),
        ]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["sample_refs"],
            [
                sample_ref("ads", "u1"),
                sample_ref("dwd", "t1"),
                sample_ref("ods", "s1"),
            ],
        )

    def test_links_are_undirected(self):
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "t1"),
        ]
        forward = correlate_linked_violations(
            results, BASE_LINEAGE, [link("ods", "s1", "dwd", "t1")]
        )
        reverse = correlate_linked_violations(
            results, BASE_LINEAGE, [link("dwd", "t1", "ods", "s1")]
        )
        self.assertEqual(forward, reverse)

    def test_passing_sample_still_bridges_violated_samples(self):
        # The pass-only sample "mid" links two violated samples; closure
        # merges them, but "mid" itself never appears in sample_refs.
        results = [
            result("r1", "ods", "name", "a"),
            result("r2", "dwd", "label", "b"),
            result("r0", "ads", "label", "mid", violated=False),
        ]
        links = [
            link("ods", "a", "ads", "mid"),
            link("ads", "mid", "dwd", "b"),
        ]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["sample_refs"],
            [sample_ref("dwd", "b"), sample_ref("ods", "a")],
        )

    def test_same_sample_id_in_different_datasets_are_distinct(self):
        results = [
            result("r1", "ods", "name", "x"),
            result("r2", "dwd", "label", "x"),
        ]
        # No link: equal sample ids across datasets do not auto-connect.
        events = correlate_linked_violations(results, BASE_LINEAGE, [])[
            "events"
        ]
        self.assertEqual(len(events), 2)

    def test_unlinked_samples_stay_in_separate_events(self):
        results = [
            result("r1", "ods", "name", "s1"),
            result("r2", "dwd", "label", "t1"),
        ]
        events = correlate_linked_violations(results, BASE_LINEAGE, [])[
            "events"
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["sample_refs"], [sample_ref("dwd", "t1")])
        self.assertEqual(events[1]["sample_refs"], [sample_ref("ods", "s1")])

    def test_sample_refs_deduplicated(self):
        # Two violated fields on the same linked sample; the sample ref
        # must appear once in the merged event.
        results = [
            result("r1", "dwd", "label", "s1"),
            result("r2", "dwd", "label", "s1"),
            result("r3", "ods", "name", "t1"),
        ]
        links = [link("dwd", "s1", "ods", "t1")]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["sample_refs"],
            [sample_ref("dwd", "s1"), sample_ref("ods", "t1")],
        )
        self.assertEqual(events[0]["rules"], ["r1", "r2", "r3"])


class LinkedCorrelateFieldGroupingTest(unittest.TestCase):
    NO_EDGE_LINEAGE = lineage(
        ["dwd"], {"dwd": ["id", "label"]}, []
    )

    def test_non_adjacent_fields_split_into_events_within_component(self):
        results = [
            result("r1", "dwd", "id", "a"),
            result("r2", "dwd", "label", "b"),
        ]
        links = [link("dwd", "a", "dwd", "b")]
        events = correlate_linked_violations(
            results, self.NO_EDGE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0]["sample_refs"], [sample_ref("dwd", "a")]
        )
        self.assertEqual(events[0]["fields"], [field_ref("dwd", "id")])
        self.assertEqual(
            events[1]["sample_refs"], [sample_ref("dwd", "b")]
        )
        self.assertEqual(events[1]["fields"], [field_ref("dwd", "label")])

    def test_adjacent_fields_merge_across_linked_samples(self):
        results = [
            result("r1", "ods", "name", "a"),
            result("r2", "ads", "label", "b"),
        ]
        links = [link("ods", "a", "ads", "b")]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        # ods.name and ads.label connect only through dwd.label, which is
        # not violated here, so they are not lineage-adjacent and split.
        self.assertEqual(len(events), 2)

    def test_context_fields_exclude_event_fields(self):
        results = [
            result("r1", "ods", "name", "a"),
            result("r2", "dwd", "label", "b"),
        ]
        links = [link("ods", "a", "dwd", "b")]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["upstream_fields"], [])
        self.assertEqual(
            events[0]["downstream_fields"], [field_ref("ads", "label")]
        )

    def test_rules_deduplicated_and_sorted(self):
        results = [
            result("r9", "dwd", "label", "a"),
            result("r1", "dwd", "label", "a"),
            result("r5", "dwd", "label", "b"),
        ]
        links = [link("dwd", "a", "dwd", "b")]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, links
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rules"], ["r1", "r5", "r9"])


class LinkedCorrelateDeterminismTest(unittest.TestCase):
    def test_events_sorted_by_refs_then_fields(self):
        results = [
            result("r1", "dwd", "id", "zzz"),
            result("r2", "dwd", "id", "aaa"),
            result("r3", "dwd", "label", "mmm"),
        ]
        lineage_graph = lineage(["dwd"], {"dwd": ["id", "label"]}, [])
        events = correlate_linked_violations(
            results, lineage_graph, []
        )["events"]
        self.assertEqual(
            [event["sample_refs"] for event in events],
            [
                [sample_ref("dwd", "aaa")],
                [sample_ref("dwd", "mmm")],
                [sample_ref("dwd", "zzz")],
            ],
        )

    def test_deterministic_output(self):
        results = [
            result("r2", "ads", "label", "c"),
            result("r1", "ods", "name", "a"),
            result("r3", "dwd", "label", "b"),
        ]
        links = [
            link("ods", "a", "dwd", "b"),
            link("dwd", "b", "ads", "c"),
        ]
        first = correlate_linked_violations(results, BASE_LINEAGE, links)
        second = correlate_linked_violations(
            list(reversed(results)),
            BASE_LINEAGE,
            list(reversed(links)),
        )
        self.assertEqual(first, second)

    def test_input_is_not_mutated(self):
        results = [result("r1", "dwd", "label", "s1", value=[1, 2])]
        links = [
            link("dwd", "s1", "ods", "s2"),
        ]
        results.append(result("r2", "ods", "name", "s2", value={"k": 1}))
        graph_snapshot = copy.deepcopy(BASE_LINEAGE)
        results_snapshot = copy.deepcopy(results)
        links_snapshot = copy.deepcopy(links)
        correlate_linked_violations(results, BASE_LINEAGE, links)
        self.assertEqual(results, results_snapshot)
        self.assertEqual(links, links_snapshot)
        self.assertEqual(BASE_LINEAGE, graph_snapshot)


class LinkedCorrelateLinksValidationTest(unittest.TestCase):
    RESULTS = [
        result("r1", "dwd", "label", "s1"),
        result("r2", "ods", "name", "s2"),
    ]

    def assert_invalid(self, sample_links):
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations(
                self.RESULTS, BASE_LINEAGE, sample_links
            )

    def test_sample_links_must_be_a_list(self):
        self.assert_invalid(None)
        self.assert_invalid({})
        self.assert_invalid("nope")

    def test_link_must_be_an_object(self):
        self.assert_invalid(["nope"])
        self.assert_invalid([42])

    def test_link_keys_are_strict(self):
        bad = {"left": sample_ref("dwd", "s1")}
        self.assert_invalid([bad])
        bad = {
            "left": sample_ref("dwd", "s1"),
            "right": sample_ref("ods", "s2"),
            "extra": 1,
        }
        self.assert_invalid([bad])

    def test_endpoint_keys_are_strict(self):
        bad = {
            "left": {"dataset_id": "dwd"},
            "right": sample_ref("ods", "s2"),
        }
        self.assert_invalid([bad])
        bad = {
            "left": {**sample_ref("dwd", "s1"), "sample_id_extra": "x"},
            "right": sample_ref("ods", "s2"),
        }
        self.assert_invalid([bad])

    def test_endpoint_must_be_object(self):
        self.assert_invalid(
            [{"left": ["dwd", "s1"], "right": sample_ref("ods", "s2")}]
        )

    def test_empty_identifiers_raise(self):
        for key in ("dataset_id", "sample_id"):
            for value in ("", None, 7):
                endpoint_obj = sample_ref("dwd", "s1")
                endpoint_obj[key] = value
                self.assert_invalid(
                    [{"left": endpoint_obj, "right": sample_ref("ods", "s2")}]
                )

    def test_self_link_raises(self):
        self.assert_invalid([link("dwd", "s1", "dwd", "s1")])

    def test_duplicate_relationship_raises(self):
        first = link("dwd", "s1", "ods", "s2")
        self.assert_invalid([first, dict(first)])

    def test_reversed_duplicate_relationship_raises(self):
        self.assert_invalid(
            [
                link("dwd", "s1", "ods", "s2"),
                link("ods", "s2", "dwd", "s1"),
            ]
        )


class LinkedCorrelateReferenceTest(unittest.TestCase):
    RESULTS = [
        result("r1", "dwd", "label", "s1"),
        result("r2", "ods", "name", "s2"),
    ]

    def test_unknown_endpoint_raises_lookup_error(self):
        with self.assertRaises(UnknownLinkedCorrelationReferenceError):
            correlate_linked_violations(
                self.RESULTS,
                BASE_LINEAGE,
                [link("ghost", "s1", "dwd", "s1")],
            )
        with self.assertRaises(UnknownLinkedCorrelationReferenceError):
            correlate_linked_violations(
                self.RESULTS,
                BASE_LINEAGE,
                [link("dwd", "ghost", "dwd", "s1")],
            )

    def test_lookup_error_is_not_value_error(self):
        with self.assertRaises(LookupError):
            correlate_linked_violations(
                self.RESULTS,
                BASE_LINEAGE,
                [link("dwd", "ghost", "dwd", "s1")],
            )
        self.assertFalse(
            issubclass(
                UnknownLinkedCorrelationReferenceError, ValueError
            )
        )
        self.assertTrue(
            issubclass(InvalidLinkedCorrelationInputError, ValueError)
        )

    def test_passing_result_is_a_valid_endpoint(self):
        results = [
            result("r1", "dwd", "label", "s1"),
            result("r2", "ods", "name", "s2", violated=False),
        ]
        events = correlate_linked_violations(
            results, BASE_LINEAGE, [link("dwd", "s1", "ods", "s2")]
        )["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sample_refs"], [sample_ref("dwd", "s1")])

    def test_structural_error_precedes_unknown_reference(self):
        # Self link whose endpoint is also unknown: structural wins.
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations(
                self.RESULTS,
                BASE_LINEAGE,
                [{"left": sample_ref("ghost", "x"),
                  "right": sample_ref("ghost", "x")}],
            )
        # Missing key beats the unknown endpoint it contains.
        with self.assertRaises(InvalidLinkedCorrelationInputError):
            correlate_linked_violations(
                self.RESULTS,
                BASE_LINEAGE,
                [{"left": {"dataset_id": "ghost"}}],
            )


class LinkedCorrelateReusesValidationTest(unittest.TestCase):
    def test_results_and_lineage_still_validated(self):
        with self.assertRaises(InvalidCorrelationInputError):
            correlate_linked_violations("nope", BASE_LINEAGE, [])
        with self.assertRaises(InvalidCorrelationInputError):
            correlate_linked_violations(
                [result("r1", "dwd", "label", "s1")], None, []
            )

    def test_unknown_result_reference_still_raises(self):
        with self.assertRaises(UnknownCorrelationReferenceError):
            correlate_linked_violations(
                [result("r1", "ghost", "label", "s1")], BASE_LINEAGE, []
            )

    def test_endpoints_match_on_dataset_and_sample_only(self):
        # Two rules on the same (dataset, sample) carry different values;
        # a link endpoint matches regardless of value.
        results = [
            result("r1", "dwd", "label", "s1", value="a"),
            result("r2", "dwd", "id", "s1", value=999),
            result("r3", "ods", "name", "s2", value="b"),
        ]
        events = correlate_linked_violations(
            results,
            BASE_LINEAGE,
            [link("dwd", "s1", "ods", "s2")],
        )["events"]
        # dwd.label and ods.name are lineage-adjacent and merge, carrying
        # both sample refs; dwd.id has no edges and stays a lone event.
        self.assertEqual(len(events), 2)
        merged = next(
            e for e in events
            if e["fields"]
            == [field_ref("dwd", "label"), field_ref("ods", "name")]
        )
        self.assertEqual(
            merged["sample_refs"],
            [sample_ref("dwd", "s1"), sample_ref("ods", "s2")],
        )
        self.assertEqual(merged["rules"], ["r1", "r3"])
        ids_event = next(
            e for e in events if e["fields"] == [field_ref("dwd", "id")]
        )
        self.assertEqual(ids_event["sample_refs"], [sample_ref("dwd", "s1")])
        self.assertEqual(ids_event["rules"], ["r2"])


if __name__ == "__main__":
    unittest.main()
