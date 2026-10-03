"""Tests for data_quality.trace_field_lineage."""

import unittest

from data_quality import (
    InvalidFieldLineageInputError,
    InvalidFieldLineageQueryError,
    UnknownFieldLineageTargetError,
    trace_field_lineage,
)


def f(table, column):
    return {"table": table, "column": column}


def edge(source, target):
    return {"source": f(*source), "target": f(*target)}


def side_keys(side):
    return [(x["table"], x["column"]) for x in side["fields"]]


def side_depths(side):
    return {
        (x["table"], x["column"]): x["depth"] for x in side["fields"]
    }


def side_edge_pairs(side):
    return [
        (
            (e["source"]["table"], e["source"]["column"]),
            (e["target"]["table"], e["target"]["column"]),
        )
        for e in side["edges"]
    ]


class BasicTraversalTest(unittest.TestCase):
    # raw.a -> ods.a -> dwd.a -> ads.a, plus ods.b -> dwd.a;
    # dwd.x is isolated.
    FIELDS = {
        "raw": ["a"],
        "ods": ["a", "b"],
        "dwd": ["a", "x"],
        "ads": ["a"],
    }
    EDGES = [
        edge(("raw", "a"), ("ods", "a")),
        edge(("ods", "a"), ("dwd", "a")),
        edge(("ods", "b"), ("dwd", "a")),
        edge(("dwd", "a"), ("ads", "a")),
    ]

    def test_both_directions_defaults(self):
        result = trace_field_lineage(self.FIELDS, self.EDGES, f("dwd", "a"))
        self.assertEqual(result["target"], {"table": "dwd", "column": "a"})
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        self.assertEqual(
            side_depths(result["upstream"]),
            {("dwd", "a"): 0, ("ods", "a"): 1, ("ods", "b"): 1,
             ("raw", "a"): 2},
        )
        # Sorted by (depth, table, column).
        self.assertEqual(
            side_keys(result["upstream"]),
            [("dwd", "a"), ("ods", "a"), ("ods", "b"), ("raw", "a")],
        )
        self.assertEqual(
            side_edge_pairs(result["upstream"]),
            [
                (("ods", "a"), ("dwd", "a")),
                (("ods", "b"), ("dwd", "a")),
                (("raw", "a"), ("ods", "a")),
            ],
        )

        self.assertEqual(
            side_depths(result["downstream"]),
            {("dwd", "a"): 0, ("ads", "a"): 1},
        )
        self.assertEqual(
            side_keys(result["downstream"]), [("dwd", "a"), ("ads", "a")]
        )
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [(("dwd", "a"), ("ads", "a"))],
        )

    def test_every_edge_between_reached_fields_is_kept(self):
        # t <- a <- b <- c, plus c -> a (a non-tree edge between reached
        # fields); it must still appear.
        fields = {
            "tbl": ["t", "a", "b", "c"],
        }
        edges = [
            edge(("tbl", "a"), ("tbl", "t")),
            edge(("tbl", "b"), ("tbl", "a")),
            edge(("tbl", "c"), ("tbl", "b")),
            edge(("tbl", "c"), ("tbl", "a")),
        ]
        result = trace_field_lineage(
            fields, edges, f("tbl", "t"), direction="upstream"
        )
        self.assertEqual(
            side_depths(result["upstream"]),
            {("tbl", "t"): 0, ("tbl", "a"): 1, ("tbl", "b"): 2,
             ("tbl", "c"): 2},
        )
        self.assertEqual(
            side_edge_pairs(result["upstream"]),
            [
                (("tbl", "a"), ("tbl", "t")),
                (("tbl", "b"), ("tbl", "a")),
                (("tbl", "c"), ("tbl", "a")),
                (("tbl", "c"), ("tbl", "b")),
            ],
        )

    def test_upstream_only_leaves_downstream_empty(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("dwd", "a"), direction="upstream"
        )
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["downstream"], {"fields": [], "edges": []})
        self.assertIn(("dwd", "a"), side_keys(result["upstream"]))

    def test_downstream_only_leaves_upstream_empty(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("dwd", "a"), direction="downstream"
        )
        self.assertEqual(result["upstream"], {"fields": [], "edges": []})
        self.assertEqual(
            side_keys(result["downstream"]), [("dwd", "a"), ("ads", "a")]
        )

    def test_isolated_field_has_no_relatives(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("dwd", "x")
        )
        self.assertEqual(
            result["upstream"]["fields"],
            [{"table": "dwd", "column": "x", "depth": 0}],
        )
        self.assertEqual(
            result["downstream"]["fields"],
            [{"table": "dwd", "column": "x", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"]["edges"], [])

    def test_target_field_objects_have_table_column_depth(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("ads", "a"), direction="upstream"
        )
        target_fields = [
            x for x in result["upstream"]["fields"]
            if x["table"] == "ads" and x["column"] == "a"
        ]
        self.assertEqual(
            target_fields, [{"table": "ads", "column": "a", "depth": 0}]
        )

    def test_edges_are_original_objects(self):
        original = self.EDGES[0]
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("ods", "a")
        )
        upstream_edges = result["upstream"]["edges"]
        self.assertIs(upstream_edges[0], original)


class DepthTest(unittest.TestCase):
    # t -> a -> b -> c, plus t -> c (shortcut) and x feeding a.
    FIELDS = {"g": ["t", "a", "b", "c", "x"]}
    EDGES = [
        edge(("g", "t"), ("g", "a")),
        edge(("g", "a"), ("g", "b")),
        edge(("g", "b"), ("g", "c")),
        edge(("g", "t"), ("g", "c")),
        edge(("g", "x"), ("g", "a")),
    ]

    def test_shortest_depth_wins(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("g", "t"), direction="downstream"
        )
        depths = side_depths(result["downstream"])
        self.assertEqual(
            depths,
            {("g", "t"): 0, ("g", "a"): 1, ("g", "c"): 1, ("g", "b"): 2},
        )
        self.assertEqual(
            side_keys(result["downstream"]),
            [("g", "t"), ("g", "a"), ("g", "c"), ("g", "b")],
        )

    def test_max_depth_zero_is_target_only(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("g", "t"), max_depth=0
        )
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(side_keys(result["upstream"]), [("g", "t")])
        self.assertEqual(side_keys(result["downstream"]), [("g", "t")])
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"]["edges"], [])

    def test_max_depth_cuts_off_expansion(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("g", "t"),
            direction="downstream", max_depth=1,
        )
        self.assertEqual(
            side_depths(result["downstream"]),
            {("g", "t"): 0, ("g", "a"): 1, ("g", "c"): 1},
        )
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [
                (("g", "t"), ("g", "a")),
                (("g", "t"), ("g", "c")),
            ],
        )

    def test_null_max_depth_is_unlimited(self):
        result = trace_field_lineage(
            self.FIELDS, self.EDGES, f("g", "t"),
            direction="downstream", max_depth=None,
        )
        self.assertEqual(
            side_keys(result["downstream"]),
            [("g", "t"), ("g", "a"), ("g", "c"), ("g", "b")],
        )


class CycleTest(unittest.TestCase):
    def test_self_loop_is_legal_and_terminates(self):
        fields = {"g": ["t", "a"]}
        edges = [
            edge(("g", "t"), ("g", "t")),
            edge(("g", "t"), ("g", "a")),
            edge(("g", "a"), ("g", "a")),
        ]
        result = trace_field_lineage(
            fields, edges, f("g", "t"), direction="downstream"
        )
        self.assertEqual(
            side_depths(result["downstream"]),
            {("g", "t"): 0, ("g", "a"): 1},
        )
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [
                (("g", "a"), ("g", "a")),
                (("g", "t"), ("g", "a")),
                (("g", "t"), ("g", "t")),
            ],
        )

    def test_directed_cycle_is_legal_and_terminates(self):
        fields = {"g": ["t", "a", "b"]}
        edges = [
            edge(("g", "t"), ("g", "a")),
            edge(("g", "a"), ("g", "b")),
            edge(("g", "b"), ("g", "t")),
        ]
        result = trace_field_lineage(fields, edges, f("g", "t"))
        self.assertEqual(
            side_depths(result["downstream"]),
            {("g", "t"): 0, ("g", "a"): 1, ("g", "b"): 2},
        )
        self.assertEqual(
            side_depths(result["upstream"]),
            {("g", "t"): 0, ("g", "b"): 1, ("g", "a"): 2},
        )


class InvalidGraphInputTest(unittest.TestCase):
    def assertInputError(self, fields, edges):
        with self.assertRaises(InvalidFieldLineageInputError):
            trace_field_lineage(fields, edges, f("t", "c"))
        with self.assertRaises(ValueError):
            trace_field_lineage(fields, edges, f("t", "c"))

    def test_fields_missing_or_wrong_shape(self):
        self.assertInputError(None, [])
        self.assertInputError({}, [])
        self.assertInputError([], [])
        self.assertInputError("t", [])
        self.assertInputError({1: ["c"]}, [])
        self.assertInputError({"": ["c"]}, [])

    def test_columns_wrong_shape(self):
        self.assertInputError({"t": None}, [])
        self.assertInputError({"t": {}}, [])
        self.assertInputError({"t": []}, [])
        self.assertInputError({"t": ["a", "a"]}, [])
        self.assertInputError({"t": ["a", ""]}, [])
        self.assertInputError({"t": ["a", 1]}, [])

    def test_edges_wrong_shape(self):
        self.assertInputError({"t": ["c"]}, None)
        self.assertInputError({"t": ["c"]}, {})
        self.assertInputError({"t": ["c"]}, [["t", "t"]])

    def test_edge_keys(self):
        self.assertInputError(
            {"t": ["c"]}, [{"source": f("t", "c")}]
        )
        self.assertInputError(
            {"t": ["c"]}, [{"target": f("t", "c")}]
        )
        self.assertInputError({"t": ["c"]}, [{}])
        self.assertInputError(
            {"t": ["c"]},
            [{"source": f("t", "c"), "target": f("t", "c"), "weight": 1}],
        )

    def test_endpoint_keys(self):
        self.assertInputError(
            {"t": ["c"]}, [{"source": {"table": "t"}, "target": f("t", "c")}]
        )
        self.assertInputError(
            {"t": ["c"]},
            [{"source": f("t", "c"),
              "target": {"table": "t", "column": "c", "x": 1}}],
        )
        self.assertInputError(
            {"t": ["c"]},
            [{"source": "not-an-object", "target": f("t", "c")}],
        )

    def test_endpoints_must_be_declared(self):
        self.assertInputError(
            {"t": ["c"]}, [edge(("t", "c"), ("x", "c"))]
        )
        self.assertInputError(
            {"t": ["c"]}, [edge(("x", "c"), ("t", "c"))]
        )
        self.assertInputError(
            {"t": ["c"]}, [edge(("t", "x"), ("t", "c"))]
        )
        self.assertInputError(
            {"t": ["c"]}, [edge(("t", "c"), ("t", "y"))]
        )

    def test_duplicate_edges_rejected_but_self_loop_allowed_once(self):
        self.assertInputError(
            {"g": ["a", "b"]},
            [edge(("g", "a"), ("g", "b")), edge(("g", "a"), ("g", "b"))],
        )
        # Opposite direction is a distinct edge.
        result = trace_field_lineage(
            {"g": ["a", "b"]},
            [edge(("g", "a"), ("g", "b")), edge(("g", "b"), ("g", "a"))],
            f("g", "a"),
        )
        self.assertEqual(
            side_keys(result["downstream"]), [("g", "a"), ("g", "b")]
        )

    def test_same_column_name_in_different_tables_is_distinct(self):
        fields = {"t1": ["id"], "t2": ["id"]}
        edges = [edge(("t1", "id"), ("t2", "id"))]
        result = trace_field_lineage(
            fields, edges, f("t1", "id"), direction="downstream"
        )
        self.assertEqual(
            side_keys(result["downstream"]), [("t1", "id"), ("t2", "id")]
        )


class InvalidQueryTest(unittest.TestCase):
    FIELDS = {"t": ["a", "b"]}
    EDGES = [edge(("t", "a"), ("t", "b"))]

    def test_target_must_be_field_reference(self):
        for bad in (None, 1, 1.0, True, "t.a", ["t", "a"], {"id": "t"}):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldLineageQueryError):
                    trace_field_lineage(self.FIELDS, self.EDGES, bad)
                with self.assertRaises(ValueError):
                    trace_field_lineage(self.FIELDS, self.EDGES, bad)

    def test_target_keys(self):
        for bad in (
            {"table": "t"},
            {"column": "a"},
            {},
            {"table": "t", "column": "a", "extra": 1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldLineageQueryError):
                    trace_field_lineage(self.FIELDS, self.EDGES, bad)

    def test_target_parts_must_be_strings(self):
        for bad in (
            {"table": 1, "column": "a"},
            {"table": "t", "column": None},
            {"table": True, "column": "a"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldLineageQueryError):
                    trace_field_lineage(self.FIELDS, self.EDGES, bad)

    def test_direction_enum(self):
        for bad in (None, "", "UPSTREAM", "both ", 1, True):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldLineageQueryError):
                    trace_field_lineage(
                        self.FIELDS, self.EDGES, f("t", "a"), direction=bad
                    )

    def test_max_depth_rules(self):
        for bad in (-1, 1.0, "1", True, False, [0]):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldLineageQueryError):
                    trace_field_lineage(
                        self.FIELDS, self.EDGES, f("t", "a"), max_depth=bad
                    )

    def test_undeclared_target(self):
        with self.assertRaises(UnknownFieldLineageTargetError):
            trace_field_lineage(self.FIELDS, self.EDGES, f("ghost", "a"))
        with self.assertRaises(UnknownFieldLineageTargetError):
            trace_field_lineage(self.FIELDS, self.EDGES, f("t", "ghost"))
        with self.assertRaises(ValueError):
            trace_field_lineage(self.FIELDS, self.EDGES, f("ghost", "a"))

    def test_graph_errors_take_precedence_over_query(self):
        with self.assertRaises(InvalidFieldLineageInputError):
            trace_field_lineage(
                {}, [], f("t", "c"), direction="nope", max_depth=-1
            )

    def test_query_errors_take_precedence_over_unknown_target(self):
        with self.assertRaises(InvalidFieldLineageQueryError):
            trace_field_lineage(
                self.FIELDS, self.EDGES, f("ghost", "a"),
                direction="sideways",
            )
        with self.assertRaises(InvalidFieldLineageQueryError):
            trace_field_lineage(
                self.FIELDS, self.EDGES, {"table": "ghost"},
                direction="both",
            )


if __name__ == "__main__":
    unittest.main()
