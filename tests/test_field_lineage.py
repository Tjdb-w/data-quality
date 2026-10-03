"""Tests for :func:`data_quality.trace_field_lineage`."""

import unittest

from data_quality import (
    InvalidFieldLineageInputError,
    InvalidFieldLineageQueryError,
    UnknownFieldLineageTargetError,
    trace_field_lineage,
)

FIELDS = {
    "raw": ["id", "name"],
    "ods": ["id", "name"],
    "dwd": ["id", "label"],
    "ads": ["label"],
}


def field(table, column):
    return {"table": table, "column": column}


def edge(src_table, src_column, dst_table, dst_column):
    return {
        "source": field(src_table, src_column),
        "target": field(dst_table, dst_column),
    }


EDGES = [
    edge("raw", "id", "ods", "id"),
    edge("raw", "name", "ods", "name"),
    edge("ods", "id", "dwd", "id"),
    edge("ods", "name", "dwd", "label"),
    edge("dwd", "label", "ads", "label"),
]


class TraceFieldLineageSuccessTest(unittest.TestCase):
    def test_both_directions_unlimited(self):
        result = trace_field_lineage(FIELDS, EDGES, field("dwd", "label"))
        self.assertEqual(result["target"], field("dwd", "label"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        upstream = result["upstream"]
        self.assertEqual(
            upstream["fields"],
            [
                {"table": "dwd", "column": "label", "depth": 0},
                {"table": "ods", "column": "name", "depth": 1},
                {"table": "raw", "column": "name", "depth": 2},
            ],
        )
        self.assertEqual(
            upstream["edges"],
            [
                edge("ods", "name", "dwd", "label"),
                edge("raw", "name", "ods", "name"),
            ],
        )

        downstream = result["downstream"]
        self.assertEqual(
            downstream["fields"],
            [
                {"table": "dwd", "column": "label", "depth": 0},
                {"table": "ads", "column": "label", "depth": 1},
            ],
        )
        self.assertEqual(downstream["edges"], [edge("dwd", "label", "ads", "label")])

    def test_fields_sorted_by_depth_table_column(self):
        result = trace_field_lineage(FIELDS, EDGES, field("ods", "id"))
        self.assertEqual(
            result["upstream"]["fields"],
            [
                {"table": "ods", "column": "id", "depth": 0},
                {"table": "raw", "column": "id", "depth": 1},
            ],
        )
        # dwd.id and (via dwd.label) ads.label are downstream.
        self.assertEqual(
            result["downstream"]["fields"],
            [
                {"table": "ods", "column": "id", "depth": 0},
                {"table": "dwd", "column": "id", "depth": 1},
            ],
        )

    def test_unqueried_side_is_empty(self):
        result = trace_field_lineage(
            FIELDS, EDGES, field("dwd", "label"), direction="upstream"
        )
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["downstream"], {"fields": [], "edges": []})

        result = trace_field_lineage(
            FIELDS, EDGES, field("dwd", "label"), direction="downstream"
        )
        self.assertEqual(result["upstream"], {"fields": [], "edges": []})
        self.assertEqual(len(result["downstream"]["fields"]), 2)

    def test_max_depth_zero_returns_only_target(self):
        result = trace_field_lineage(
            FIELDS, EDGES, field("dwd", "label"), max_depth=0
        )
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(
            result["upstream"]["fields"],
            [{"table": "dwd", "column": "label", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(
            result["downstream"]["fields"],
            [{"table": "dwd", "column": "label", "depth": 0}],
        )
        self.assertEqual(result["downstream"]["edges"], [])

    def test_max_depth_limits_traversal(self):
        result = trace_field_lineage(
            FIELDS, EDGES, field("dwd", "label"), direction="upstream",
            max_depth=1,
        )
        self.assertEqual(
            result["upstream"]["fields"],
            [
                {"table": "dwd", "column": "label", "depth": 0},
                {"table": "ods", "column": "name", "depth": 1},
            ],
        )
        self.assertEqual(
            result["upstream"]["edges"], [edge("ods", "name", "dwd", "label")]
        )

    def test_shortest_depth_wins_and_all_reachable_edges_kept(self):
        fields = {"t": ["a", "b", "c", "d"]}
        edges = [
            edge("t", "a", "t", "b"),
            edge("t", "a", "t", "c"),
            edge("t", "b", "t", "d"),
            edge("t", "c", "t", "d"),
            edge("t", "a", "t", "d"),
        ]
        result = trace_field_lineage(
            fields, edges, field("t", "a"), direction="downstream"
        )
        self.assertEqual(
            result["downstream"]["fields"],
            [
                {"table": "t", "column": "a", "depth": 0},
                {"table": "t", "column": "b", "depth": 1},
                {"table": "t", "column": "c", "depth": 1},
                {"table": "t", "column": "d", "depth": 1},
            ],
        )
        # Every original edge between reached fields, sorted by endpoints.
        self.assertEqual(
            result["downstream"]["edges"],
            [
                edge("t", "a", "t", "b"),
                edge("t", "a", "t", "c"),
                edge("t", "a", "t", "d"),
                edge("t", "b", "t", "d"),
                edge("t", "c", "t", "d"),
            ],
        )

    def test_self_loop_and_cycle_are_legal(self):
        fields = {"t": ["a", "b"]}
        edges = [
            edge("t", "a", "t", "a"),
            edge("t", "a", "t", "b"),
            edge("t", "b", "t", "a"),
        ]
        result = trace_field_lineage(fields, edges, field("t", "a"))
        self.assertEqual(
            result["downstream"]["fields"],
            [
                {"table": "t", "column": "a", "depth": 0},
                {"table": "t", "column": "b", "depth": 1},
            ],
        )
        self.assertEqual(result["downstream"]["edges"], edges)
        self.assertEqual(result["upstream"]["edges"], edges)

    def test_isolated_target_has_no_edges(self):
        result = trace_field_lineage(FIELDS, EDGES, field("raw", "id"),
                                     direction="upstream")
        self.assertEqual(
            result["upstream"]["fields"],
            [{"table": "raw", "column": "id", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])


class TraceFieldLineageInputErrorTest(unittest.TestCase):
    def assert_input_error(self, fields, edges):
        with self.assertRaises(InvalidFieldLineageInputError):
            trace_field_lineage(fields, edges, field("raw", "id"))

    def test_fields_must_be_object(self):
        self.assert_input_error(None, [])
        self.assert_input_error([], [])
        self.assert_input_error(["raw"], [])

    def test_table_keys_must_be_non_empty_strings(self):
        self.assert_input_error({"": ["a"]}, [])
        self.assert_input_error({1: ["a"]}, [])

    def test_field_ids_must_be_non_empty_distinct_strings(self):
        self.assert_input_error({"t": "a"}, [])
        self.assert_input_error({"t": [""]}, [])
        self.assert_input_error({"t": [1]}, [])
        self.assert_input_error({"t": ["a", "a"]}, [])

    def test_edges_must_be_list_of_edge_objects(self):
        self.assert_input_error(FIELDS, None)
        self.assert_input_error(FIELDS, ["x"])
        self.assert_input_error(FIELDS, [{"source": field("raw", "id")}])
        self.assert_input_error(
            FIELDS,
            [dict(edge("raw", "id", "ods", "id"), extra=1)],
        )

    def test_endpoints_must_be_declared_field_objects(self):
        self.assert_input_error(FIELDS, [edge("raw", "id", "ghost", "id")])
        self.assert_input_error(FIELDS, [edge("raw", "ghost", "ods", "id")])
        self.assert_input_error(
            FIELDS, [{"source": "raw", "target": field("ods", "id")}]
        )
        self.assert_input_error(
            FIELDS,
            [{
                "source": {"table": "raw", "column": "id", "x": 1},
                "target": field("ods", "id"),
            }],
        )
        self.assert_input_error(
            FIELDS,
            [{"source": {"table": "raw"}, "target": field("ods", "id")}],
        )
        self.assert_input_error(
            FIELDS,
            [{
                "source": {"table": "", "column": "id"},
                "target": field("ods", "id"),
            }],
        )

    def test_duplicate_edges_rejected(self):
        self.assert_input_error(
            FIELDS,
            [edge("raw", "id", "ods", "id"), edge("raw", "id", "ods", "id")],
        )


class TraceFieldLineageQueryErrorTest(unittest.TestCase):
    def assert_query_error(self, target, direction="both", max_depth=None):
        with self.assertRaises(InvalidFieldLineageQueryError):
            trace_field_lineage(FIELDS, EDGES, target, direction, max_depth)

    def test_target_must_be_table_column_object(self):
        self.assert_query_error(None)
        self.assert_query_error("raw.id")
        self.assert_query_error({"table": "raw"})
        self.assert_query_error({"table": "raw", "column": "id", "x": 1})
        self.assert_query_error({"table": "", "column": "id"})
        self.assert_query_error({"table": "raw", "column": 5})

    def test_direction_must_be_known(self):
        self.assert_query_error(field("raw", "id"), direction="sideways")
        self.assert_query_error(field("raw", "id"), direction=None)

    def test_max_depth_must_be_null_or_non_negative_int(self):
        self.assert_query_error(field("raw", "id"), max_depth=-1)
        self.assert_query_error(field("raw", "id"), max_depth=True)
        self.assert_query_error(field("raw", "id"), max_depth=1.5)
        self.assert_query_error(field("raw", "id"), max_depth="1")


class TraceFieldLineageUnknownTargetTest(unittest.TestCase):
    def test_undeclared_target(self):
        with self.assertRaises(UnknownFieldLineageTargetError):
            trace_field_lineage(FIELDS, EDGES, field("ghost", "id"))
        with self.assertRaises(UnknownFieldLineageTargetError):
            trace_field_lineage(FIELDS, EDGES, field("raw", "ghost"))

    def test_validation_order(self):
        # Bad graph beats bad query and unknown target.
        with self.assertRaises(InvalidFieldLineageInputError):
            trace_field_lineage(
                {"t": ["a", "a"]}, [], "nope", direction="sideways"
            )
        # Bad query beats unknown target.
        with self.assertRaises(InvalidFieldLineageQueryError):
            trace_field_lineage(
                FIELDS, EDGES, field("ghost", "id"), direction="sideways"
            )


if __name__ == "__main__":
    unittest.main()
