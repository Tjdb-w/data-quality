"""Tests for :func:`data_quality.explain_field_lineage_paths`."""

import unittest

from data_quality import (
    InvalidFieldLineageInputError,
    InvalidFieldLineageQueryError,
    LineageNodeNotFoundError,
    explain_field_lineage_paths,
)


def field(table, column):
    return {"table": table, "column": column}


def edge(src_table, src_column, dst_table, dst_column):
    return {
        "source": field(src_table, src_column),
        "target": field(dst_table, dst_column),
    }


FIELDS = {
    "raw": ["id", "name"],
    "ods": ["id", "name"],
    "dwd": ["id", "label"],
    "ads": ["label"],
    "iso": ["x"],
}

EDGES = [
    edge("raw", "id", "ods", "id"),
    edge("raw", "name", "ods", "name"),
    edge("ods", "id", "dwd", "id"),
    edge("ods", "name", "dwd", "label"),
    edge("dwd", "label", "ads", "label"),
]

EMPTY_SIDE = {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []}


def path_refs(path):
    return [
        (step["node"]["table"], step["node"]["column"]) for step in path["nodes"]
    ]


class FieldPathSuccessTest(unittest.TestCase):
    def test_upstream_chain_ordered_target_to_source(self):
        result = explain_field_lineage_paths(
            FIELDS, EDGES, field("dwd", "label"), direction="upstream"
        )
        side = result["upstream"]
        self.assertEqual(result["target"], field("dwd", "label"))
        self.assertFalse(side["cycle"])
        self.assertEqual(
            [path_refs(p) for p in side["paths"]],
            [
                [("dwd", "label"), ("ods", "name"), ("raw", "name")],
            ],
        )
        path = side["paths"][0]
        self.assertEqual(path["depth"], 2)
        self.assertEqual(
            [step["node"] for step in path["nodes"]],
            [
                {"table": "dwd", "column": "label", "depth": 0},
                {"table": "ods", "column": "name", "depth": 1},
                {"table": "raw", "column": "name", "depth": 2},
            ],
        )
        self.assertIsNone(path["nodes"][0]["edge"])
        self.assertEqual(path["nodes"][1]["edge"], edge("ods", "name", "dwd", "label"))
        self.assertEqual(path["nodes"][2]["edge"], edge("raw", "name", "ods", "name"))

    def test_downstream_impact_scope_includes_indirect_dependents(self):
        # raw.name propagates through ods.name/dwd.label all the way to
        # ads.label, which must be present as a depth-3 impact path.
        result = explain_field_lineage_paths(
            FIELDS, EDGES, field("raw", "name"), direction="downstream"
        )
        self.assertEqual(
            [path_refs(p) for p in result["downstream"]["paths"]],
            [
                [
                    ("raw", "name"),
                    ("ods", "name"),
                    ("dwd", "label"),
                    ("ads", "label"),
                ]
            ],
        )

    def test_multiple_upstream_paths_sorted_by_depth_then_ids(self):
        fields = {
            "raw1": ["x"],
            "raw2": ["x"],
            "ods": ["a", "b"],
            "dwd": ["t"],
        }
        edges = [
            edge("raw1", "x", "ods", "a"),
            edge("raw2", "x", "ods", "b"),
            edge("ods", "a", "dwd", "t"),
            edge("ods", "b", "dwd", "t"),
        ]
        result = explain_field_lineage_paths(
            fields, edges, field("dwd", "t"), direction="upstream"
        )
        self.assertEqual(
            [path_refs(p) for p in result["upstream"]["paths"]],
            [
                [("dwd", "t"), ("ods", "a"), ("raw1", "x")],
                [("dwd", "t"), ("ods", "b"), ("raw2", "x")],
            ],
        )

    def test_no_relations_is_empty_success(self):
        result = explain_field_lineage_paths(
            FIELDS, EDGES, field("ads", "label"), direction="downstream"
        )
        self.assertEqual(result["downstream"], EMPTY_SIDE)

        isolated = explain_field_lineage_paths(
            FIELDS, EDGES, field("iso", "x")
        )
        self.assertEqual(isolated["upstream"], EMPTY_SIDE)
        self.assertEqual(isolated["downstream"], EMPTY_SIDE)

    def test_unqueried_side_is_empty_placeholder(self):
        result = explain_field_lineage_paths(
            FIELDS, EDGES, field("dwd", "label"), direction="upstream"
        )
        self.assertEqual(result["downstream"], EMPTY_SIDE)


class FieldCycleTest(unittest.TestCase):
    def test_field_cycle_reports_nodes_and_edges_keeps_independent_path(self):
        fields = {"t": ["f"], "a": ["f"], "b": ["f"], "c": ["f"]}
        edges = [
            edge("t", "f", "a", "f"),
            edge("a", "f", "b", "f"),
            edge("b", "f", "a", "f"),
            edge("t", "f", "c", "f"),
        ]
        result = explain_field_lineage_paths(
            fields, edges, field("t", "f"), direction="downstream"
        )
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(
            side["cycle_nodes"],
            [field("a", "f"), field("b", "f")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in side["cycle_edges"]],
            [
                (field("a", "f"), field("b", "f")),
                (field("b", "f"), field("a", "f")),
            ],
        )
        self.assertEqual(
            sorted(path_refs(p) for p in side["paths"]),
            [
                [("t", "f"), ("a", "f"), ("b", "f")],
                [("t", "f"), ("c", "f")],
            ],
        )
        for path in side["paths"]:
            refs = path_refs(path)
            self.assertEqual(len(refs), len(set(refs)))

    def test_field_self_loop(self):
        fields = {"t": ["f"]}
        edges = [edge("t", "f", "t", "f")]
        result = explain_field_lineage_paths(
            fields, edges, field("t", "f"), direction="downstream"
        )
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], [field("t", "f")])
        self.assertEqual(side["cycle_edges"], [edge("t", "f", "t", "f")])
        self.assertEqual(side["paths"], [])


class FieldMaxDepthTest(unittest.TestCase):
    def test_zero_is_target_only_path(self):
        result = explain_field_lineage_paths(
            FIELDS, EDGES, field("dwd", "label"), max_depth=0
        )
        for side in (result["upstream"], result["downstream"]):
            self.assertEqual(len(side["paths"]), 1)
            self.assertEqual(path_refs(side["paths"][0]), [("dwd", "label")])


class FieldErrorTest(unittest.TestCase):
    def test_undeclared_target_raises_node_not_found(self):
        with self.assertRaises(LineageNodeNotFoundError) as ctx:
            explain_field_lineage_paths(
                FIELDS, EDGES, field("ads", "missing")
            )
        self.assertEqual(ctx.exception.code, "LINEAGE_NODE_NOT_FOUND")
        with self.assertRaises(LineageNodeNotFoundError):
            explain_field_lineage_paths(
                FIELDS, EDGES, field("ghost", "x")
            )

    def test_graph_errors_reuse_existing_exception(self):
        with self.assertRaises(InvalidFieldLineageInputError):
            explain_field_lineage_paths(
                "not-an-object", [], field("t", "f")
            )
        with self.assertRaises(InvalidFieldLineageInputError):
            explain_field_lineage_paths(
                {"t": ["f"]},
                [edge("t", "f", "ghost", "f")],
                field("t", "f"),
            )

    def test_query_errors_reuse_existing_exception(self):
        with self.assertRaises(InvalidFieldLineageQueryError):
            explain_field_lineage_paths(FIELDS, EDGES, "dwd.label")
        with self.assertRaises(InvalidFieldLineageQueryError):
            explain_field_lineage_paths(
                FIELDS, EDGES, field("dwd", "label"), direction="up"
            )
        with self.assertRaises(InvalidFieldLineageQueryError):
            explain_field_lineage_paths(
                FIELDS, EDGES, field("dwd", "label"), max_depth=False
            )


if __name__ == "__main__":
    unittest.main()
