"""Tests for :func:`data_quality.trace_sample_lineage`."""

import unittest

from data_quality import (
    InvalidSampleLineageInputError,
    InvalidSampleLineageQueryError,
    UnknownSampleLineageReferenceError,
    UnknownSampleLineageTargetError,
    trace_sample_lineage,
)


def sample(dataset, sample_id):
    return {"dataset_id": dataset, "sample_id": sample_id}


def field(table, column):
    return {"table": table, "column": column}


def edge(src_dataset, src_sample, dst_dataset, dst_sample, witnesses):
    return {
        "source": sample(src_dataset, src_sample),
        "target": sample(dst_dataset, dst_sample),
        "witnesses": witnesses,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample(left_dataset, left_sample),
        "right": sample(right_dataset, right_sample),
    }


SAMPLES = [
    sample("raw", "r1"),
    sample("ods", "o1"),
    sample("ods", "o2"),
    sample("dwd", "d1"),
    sample("ads", "a1"),
]

FIELDS = {
    "raw": ["id", "name"],
    "ods": ["id", "name"],
    "dwd": ["id", "label"],
    "ads": ["label"],
}

EDGES = [
    # Witnesses intentionally out of (table, column) order to verify sorting.
    edge("raw", "r1", "ods", "o1", [field("raw", "id"), field("ods", "id")]),
    edge("raw", "r1", "ods", "o2", [field("ods", "name"), field("raw", "name")]),
    edge("ods", "o1", "dwd", "d1", [field("ods", "id"), field("dwd", "id")]),
    edge("ods", "o2", "dwd", "d1", [field("dwd", "label"), field("ods", "name")]),
    edge("dwd", "d1", "ads", "a1", [field("dwd", "label"), field("ads", "label")]),
]

# Undirected link between the two ods samples.
SAMPLE_LINKS = [link("ods", "o1", "ods", "o2")]


class TraceSampleLineageSuccessTest(unittest.TestCase):
    def test_both_directions_unlimited(self):
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS, sample("dwd", "d1")
        )
        self.assertEqual(result["target"], sample("dwd", "d1"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        self.assertEqual(
            result["upstream"]["samples"],
            [
                {"dataset_id": "dwd", "sample_id": "d1", "depth": 0},
                {"dataset_id": "ods", "sample_id": "o1", "depth": 1},
                {"dataset_id": "ods", "sample_id": "o2", "depth": 1},
                {"dataset_id": "raw", "sample_id": "r1", "depth": 2},
            ],
        )
        self.assertEqual(
            result["upstream"]["edges"],
            [
                edge("ods", "o1", "dwd", "d1",
                     [field("dwd", "id"), field("ods", "id")]),
                edge("ods", "o2", "dwd", "d1",
                     [field("dwd", "label"), field("ods", "name")]),
                edge("raw", "r1", "ods", "o1",
                     [field("ods", "id"), field("raw", "id")]),
                edge("raw", "r1", "ods", "o2",
                     [field("ods", "name"), field("raw", "name")]),
            ],
        )

        self.assertEqual(
            result["downstream"]["samples"],
            [
                {"dataset_id": "dwd", "sample_id": "d1", "depth": 0},
                {"dataset_id": "ads", "sample_id": "a1", "depth": 1},
            ],
        )
        self.assertEqual(
            result["downstream"]["edges"],
            [
                edge("dwd", "d1", "ads", "a1",
                     [field("ads", "label"), field("dwd", "label")]),
            ],
        )

    def test_unqueried_side_is_empty(self):
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("dwd", "d1"), direction="upstream",
        )
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["downstream"], {"samples": [], "edges": []})

        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("dwd", "d1"), direction="downstream",
        )
        self.assertEqual(result["upstream"], {"samples": [], "edges": []})
        self.assertEqual(len(result["downstream"]["samples"]), 2)

    def test_max_depth_zero_returns_only_target(self):
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("dwd", "d1"), max_depth=0,
        )
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(
            result["upstream"]["samples"],
            [{"dataset_id": "dwd", "sample_id": "d1", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(
            result["downstream"]["samples"],
            [{"dataset_id": "dwd", "sample_id": "d1", "depth": 0}],
        )
        self.assertEqual(result["downstream"]["edges"], [])

    def test_max_depth_limits_traversal_and_edge_inclusion(self):
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("dwd", "d1"), direction="upstream", max_depth=1,
        )
        self.assertEqual(
            [tuple(s.values()) for s in result["upstream"]["samples"]],
            [("dwd", "d1", 0), ("ods", "o1", 1), ("ods", "o2", 1)],
        )
        # Only edges whose both endpoints are reached at depth <= 1.
        self.assertEqual(
            [(e["source"]["sample_id"], e["target"]["sample_id"])
             for e in result["upstream"]["edges"]],
            [("o1", "d1"), ("o2", "d1")],
        )

    def test_sample_links_are_undirected(self):
        # From o1 the link reaches o2 downstream even without a field edge
        # in that direction, and the link itself never appears as an edge.
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("ods", "o1"), direction="downstream",
        )
        samples = {
            (s["dataset_id"], s["sample_id"]): s["depth"]
            for s in result["downstream"]["samples"]
        }
        self.assertEqual(samples[("ods", "o1")], 0)
        self.assertEqual(samples[("ods", "o2")], 1)  # via the link
        self.assertEqual(samples[("dwd", "d1")], 1)
        self.assertEqual(samples[("ads", "a1")], 2)
        self.assertNotIn(("raw", "r1"), samples)  # only upstream of o1/o2
        # Output never carries links as edges: all edges have witnesses.
        self.assertTrue(
            all("witnesses" in e for e in result["downstream"]["edges"])
        )

    def test_link_bridges_directed_edge_into_reached_side(self):
        # A link from ads.a1 to raw.r1 makes the downstream-only sample
        # a1 reachable on the upstream walk too, so the d1->a1 edge is
        # kept upstream once both endpoints are reached.
        links = SAMPLE_LINKS + [link("ads", "a1", "raw", "r1")]
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, links,
            sample("dwd", "d1"), direction="upstream",
        )
        depths = {
            (s["dataset_id"], s["sample_id"]): s["depth"]
            for s in result["upstream"]["samples"]
        }
        self.assertEqual(depths[("ads", "a1")], 3)
        self.assertEqual(len(result["upstream"]["edges"]), 5)

    def test_shortest_depth_wins(self):
        samples = [sample("t", "a"), sample("t", "b"), sample("t", "c")]
        fields = {"t": ["x"]}
        edges = [
            edge("t", "a", "t", "b", []),
            edge("t", "a", "t", "c", []),
            edge("t", "b", "t", "c", []),
        ]
        result = trace_sample_lineage(
            samples, fields, edges, [], sample("t", "a"),
            direction="downstream",
        )
        self.assertEqual(
            [(s["sample_id"], s["depth"])
             for s in result["downstream"]["samples"]],
            [("a", 0), ("b", 1), ("c", 1)],
        )
        self.assertEqual(
            [(e["source"]["sample_id"], e["target"]["sample_id"])
             for e in result["downstream"]["edges"]],
            [("a", "b"), ("a", "c"), ("b", "c")],
        )

    def test_self_loop_and_cycle_are_legal(self):
        samples = [sample("t", "a"), sample("t", "b")]
        fields = {"t": ["x"]}
        edges = [
            edge("t", "a", "t", "a", []),
            edge("t", "a", "t", "b", []),
            edge("t", "b", "t", "a", []),
        ]
        result = trace_sample_lineage(
            samples, fields, edges, [], sample("t", "a"),
        )
        self.assertEqual(
            [(s["sample_id"], s["depth"])
             for s in result["downstream"]["samples"]],
            [("a", 0), ("b", 1)],
        )
        self.assertEqual(len(result["downstream"]["edges"]), 3)
        self.assertEqual(len(result["upstream"]["edges"]), 3)

    def test_empty_witnesses_and_isolated_target(self):
        result = trace_sample_lineage(
            SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
            sample("raw", "r1"), direction="upstream",
        )
        self.assertEqual(
            result["upstream"]["samples"],
            [{"dataset_id": "raw", "sample_id": "r1", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])

    def test_edges_sorted_including_witness_tiebreak(self):
        # Same endpoints may carry distinct witness sets; witnesses order
        # is the final edge sort key.
        samples = [sample("t", "a"), sample("t", "b")]
        fields = {"t": ["x", "y"]}
        edges = [
            edge("t", "a", "t", "b", [field("t", "y")]),
            edge("t", "a", "t", "b", [field("t", "x")]),
        ]
        result = trace_sample_lineage(
            samples, fields, edges, [], sample("t", "a"),
            direction="downstream",
        )
        self.assertEqual(
            [[w["column"] for w in e["witnesses"]]
             for e in result["downstream"]["edges"]],
            [["x"], ["y"]],
        )


class TraceSampleLineageInputErrorTest(unittest.TestCase):
    def assert_input_error(self, *, samples=SAMPLES, fields=FIELDS,
                           edges=EDGES, sample_links=SAMPLE_LINKS,
                           target=sample("raw", "r1")):
        with self.assertRaises(InvalidSampleLineageInputError):
            trace_sample_lineage(
                samples, fields, edges, sample_links, target
            )

    def test_fields_must_be_object_of_lists(self):
        self.assert_input_error(fields=None)
        self.assert_input_error(fields=[])
        self.assert_input_error(fields={"t": "x"})
        self.assert_input_error(fields={"": ["x"]})
        self.assert_input_error(fields={"t": [""]})
        self.assert_input_error(fields={"t": [1]})
        self.assert_input_error(fields={"t": ["x", "x"]})

    def test_samples_must_be_list_of_distinct_refs(self):
        self.assert_input_error(samples=None)
        self.assert_input_error(samples={})
        self.assert_input_error(samples=["raw/r1"])
        self.assert_input_error(samples=[{"dataset_id": "raw"}])
        self.assert_input_error(
            samples=[dict(sample("raw", "r1"), x=1)]
        )
        self.assert_input_error(
            samples=[{"dataset_id": "", "sample_id": "r1"}]
        )
        self.assert_input_error(
            samples=[{"dataset_id": "raw", "sample_id": ""}]
        )
        self.assert_input_error(
            samples=[sample("raw", "r1"), sample("raw", "r1")]
        )

    def test_edges_must_be_edge_objects(self):
        self.assert_input_error(edges=None)
        self.assert_input_error(edges=["x"])
        self.assert_input_error(
            edges=[{"source": sample("raw", "r1"),
                    "target": sample("ods", "o1")}]
        )
        self.assert_input_error(
            edges=[dict(
                edge("raw", "r1", "ods", "o1", []), extra=1
            )]
        )

    def test_edge_endpoints_must_be_declared_sample_refs(self):
        self.assert_input_error(
            edges=[edge("ghost", "r1", "ods", "o1", [])]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "ghost", [])]
        )
        self.assert_input_error(
            edges=[{
                "source": "raw/r1",
                "target": sample("ods", "o1"),
                "witnesses": [],
            }]
        )
        self.assert_input_error(
            edges=[{
                "source": {"dataset_id": "raw"},
                "target": sample("ods", "o1"),
                "witnesses": [],
            }]
        )

    def test_witnesses_must_be_declared_distinct_field_refs(self):
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1", "not-a-list")]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1", ["raw.id"])]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1", [{"table": "raw"}])]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1",
                        [dict(field("raw", "id"), x=1)])]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1",
                        [field("ghost", "id")])]
        )
        self.assert_input_error(
            edges=[edge("raw", "r1", "ods", "o1",
                        [field("raw", "id"), field("raw", "id")])]
        )

    def test_duplicate_edges_rejected(self):
        # Identical endpoints and witness set, even reordered witnesses.
        self.assert_input_error(
            edges=[
                edge("raw", "r1", "ods", "o1",
                     [field("raw", "id"), field("ods", "id")]),
                edge("raw", "r1", "ods", "o1",
                     [field("ods", "id"), field("raw", "id")]),
            ]
        )

    def test_same_endpoints_distinct_witnesses_are_allowed(self):
        edges = [
            edge("raw", "r1", "ods", "o1", [field("raw", "id")]),
            edge("raw", "r1", "ods", "o1", [field("ods", "id")]),
        ]
        result = trace_sample_lineage(
            SAMPLES, FIELDS, edges, [], sample("ods", "o1"),
            direction="upstream",
        )
        self.assertEqual(len(result["upstream"]["edges"]), 2)

    def test_sample_links_must_be_well_formed(self):
        self.assert_input_error(sample_links=None)
        self.assert_input_error(sample_links=["x"])
        self.assert_input_error(
            sample_links=[{"left": sample("ods", "o1")}]
        )
        self.assert_input_error(
            sample_links=[dict(
                link("ods", "o1", "ods", "o2"), extra=1
            )]
        )
        self.assert_input_error(
            sample_links=[{
                "left": {"dataset_id": "ods"},
                "right": sample("ods", "o2"),
            }]
        )
        self.assert_input_error(
            sample_links=[{"left": "ods/o1",
                           "right": sample("ods", "o2")}]
        )

    def test_self_links_and_duplicate_links_rejected(self):
        self.assert_input_error(
            sample_links=[link("ods", "o1", "ods", "o1")]
        )
        self.assert_input_error(
            sample_links=[
                link("ods", "o1", "ods", "o2"),
                link("ods", "o2", "ods", "o1"),
            ]
        )


class TraceSampleLineageQueryErrorTest(unittest.TestCase):
    def assert_query_error(self, target, direction="both", max_depth=None):
        with self.assertRaises(InvalidSampleLineageQueryError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES, SAMPLE_LINKS,
                target, direction, max_depth,
            )

    def test_target_must_be_dataset_sample_object(self):
        self.assert_query_error(None)
        self.assert_query_error("dwd/d1")
        self.assert_query_error({"dataset_id": "dwd"})
        self.assert_query_error(
            {"dataset_id": "dwd", "sample_id": "d1", "x": 1}
        )
        self.assert_query_error({"dataset_id": "", "sample_id": "d1"})
        self.assert_query_error({"dataset_id": "dwd", "sample_id": 5})

    def test_direction_must_be_known(self):
        self.assert_query_error(sample("dwd", "d1"), direction="sideways")
        self.assert_query_error(sample("dwd", "d1"), direction=None)

    def test_max_depth_must_be_null_or_non_negative_int(self):
        self.assert_query_error(sample("dwd", "d1"), max_depth=-1)
        self.assert_query_error(sample("dwd", "d1"), max_depth=True)
        self.assert_query_error(sample("dwd", "d1"), max_depth=1.5)
        self.assert_query_error(sample("dwd", "d1"), max_depth="1")


class TraceSampleLineageTargetAndReferenceTest(unittest.TestCase):
    def test_unknown_target(self):
        with self.assertRaises(UnknownSampleLineageTargetError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES, SAMPLE_LINKS, sample("ghost", "d1")
            )
        with self.assertRaises(UnknownSampleLineageTargetError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES, SAMPLE_LINKS, sample("dwd", "ghost")
            )

    def test_unknown_sample_link_reference(self):
        with self.assertRaises(UnknownSampleLineageReferenceError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES,
                [link("ghost", "x", "ods", "o1")],
                sample("dwd", "d1"),
            )
        with self.assertRaises(UnknownSampleLineageReferenceError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES,
                [link("ods", "o1", "dwd", "不存在")],
                sample("dwd", "d1"),
            )

    def test_reference_error_is_lookup_error(self):
        self.assertTrue(issubclass(UnknownSampleLineageReferenceError, LookupError))
        self.assertTrue(issubclass(UnknownSampleLineageTargetError, ValueError))
        self.assertTrue(issubclass(InvalidSampleLineageInputError, ValueError))
        self.assertTrue(issubclass(InvalidSampleLineageQueryError, ValueError))

    def test_validation_order(self):
        # Bad graph beats bad query, unknown target and unknown reference.
        with self.assertRaises(InvalidSampleLineageInputError):
            trace_sample_lineage(
                [sample("raw", "r1"), sample("raw", "r1")],
                FIELDS, EDGES,
                [link("ghost", "x", "ghost", "y")],
                "nope", direction="sideways", max_depth=-1,
            )
        # Bad query beats unknown target and unknown reference.
        with self.assertRaises(InvalidSampleLineageQueryError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES,
                [link("ghost", "x", "ods", "o1")],
                sample("ghost", "d1"), direction="sideways",
            )
        # Unknown target beats unknown link reference.
        with self.assertRaises(UnknownSampleLineageTargetError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES,
                [link("ghost", "x", "ods", "o1")],
                sample("ghost", "d1"),
            )
        # A structurally malformed link beats its unknown endpoint.
        with self.assertRaises(InvalidSampleLineageInputError):
            trace_sample_lineage(
                SAMPLES, FIELDS, EDGES,
                [{"left": sample("ghost", "x"),
                  "right": sample("ghost", "x")}],
                sample("dwd", "d1"),
            )


if __name__ == "__main__":
    unittest.main()
