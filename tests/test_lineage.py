"""Tests for data_quality.trace_lineage."""

import unittest

from data_quality import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    UnknownLineageTargetError,
    trace_lineage,
)


def edge(source, target):
    return {"source": source, "target": target}


def side_ids(side):
    return [node["id"] for node in side["nodes"]]


def side_depths(side):
    return {node["id"]: node["depth"] for node in side["nodes"]}


def side_edge_pairs(side):
    return [(e["source"], e["target"]) for e in side["edges"]]


class BasicTraversalTest(unittest.TestCase):
    # raw        a
    #          /   \
    # raw ->  b --> c     d (isolated)
    NODES = ["a", "b", "c", "d", "raw"]
    EDGES = [edge("a", "b"), edge("a", "c"), edge("b", "c"), edge("raw", "b")]

    def test_both_directions_defaults(self):
        result = trace_lineage(self.NODES, self.EDGES, "b")
        self.assertEqual(result["target"], "b")
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        self.assertEqual(side_depths(result["upstream"]), {"b": 0, "a": 1, "raw": 1})
        self.assertEqual(side_ids(result["upstream"]), ["b", "a", "raw"])
        self.assertEqual(
            side_edge_pairs(result["upstream"]),
            [("a", "b"), ("raw", "b")],
        )

        self.assertEqual(side_depths(result["downstream"]), {"b": 0, "c": 1})
        self.assertEqual(side_ids(result["downstream"]), ["b", "c"])
        self.assertEqual(side_edge_pairs(result["downstream"]), [("b", "c")])

    def test_every_edge_between_reached_nodes_is_kept(self):
        # a -> t, b -> a, c -> b, plus c -> a (a non-tree edge between two
        # reached nodes). c -> b connects two depth-2 nodes and must still
        # appear even though it is not on a shortest-path tree.
        nodes = ["t", "a", "b", "c"]
        edges = [edge("a", "t"), edge("b", "a"), edge("c", "b"), edge("c", "a")]
        result = trace_lineage(nodes, edges, "t", direction="upstream")
        self.assertEqual(side_depths(result["upstream"]),
                         {"t": 0, "a": 1, "b": 2, "c": 2})
        self.assertEqual(
            side_edge_pairs(result["upstream"]),
            [("a", "t"), ("b", "a"), ("c", "a"), ("c", "b")],
        )

    def test_upstream_only_leaves_downstream_empty(self):
        result = trace_lineage(self.NODES, self.EDGES, "b", direction="upstream")
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["downstream"], {"nodes": [], "edges": []})
        self.assertEqual(side_ids(result["upstream"]), ["b", "a", "raw"])

    def test_downstream_only_leaves_upstream_empty(self):
        result = trace_lineage(self.NODES, self.EDGES, "b", direction="downstream")
        self.assertEqual(result["upstream"], {"nodes": [], "edges": []})
        self.assertEqual(side_ids(result["downstream"]), ["b", "c"])

    def test_isolated_node_has_no_relatives(self):
        result = trace_lineage(self.NODES, self.EDGES, "d")
        self.assertEqual(result["upstream"]["nodes"], [{"id": "d", "depth": 0}])
        self.assertEqual(result["downstream"]["nodes"], [{"id": "d", "depth": 0}])
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"]["edges"], [])

    def test_target_node_objects_have_id_and_depth(self):
        result = trace_lineage(self.NODES, self.EDGES, "c", direction="upstream")
        target_nodes = [
            n for n in result["upstream"]["nodes"] if n["id"] == "c"
        ]
        self.assertEqual(target_nodes, [{"id": "c", "depth": 0}])


class DepthTest(unittest.TestCase):
    # t -> a -> b -> c, plus t -> c (shortcut) and x feeding c.
    NODES = ["t", "a", "b", "c", "x"]
    EDGES = [
        edge("t", "a"),
        edge("a", "b"),
        edge("b", "c"),
        edge("t", "c"),
        edge("x", "a"),
    ]

    def test_shortest_depth_wins(self):
        result = trace_lineage(self.NODES, self.EDGES, "t", direction="downstream")
        depths = side_depths(result["downstream"])
        self.assertEqual(depths, {"t": 0, "a": 1, "c": 1, "b": 2})
        # Sorted by (depth, id): depth 0 t, depth 1 a/c, depth 2 b.
        self.assertEqual(side_ids(result["downstream"]), ["t", "a", "c", "b"])

    def test_max_depth_zero_is_target_only(self):
        result = trace_lineage(
            self.NODES, self.EDGES, "t", max_depth=0
        )
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(side_ids(result["upstream"]), ["t"])
        self.assertEqual(side_ids(result["downstream"]), ["t"])
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"]["edges"], [])

    def test_max_depth_cuts_off_expansion(self):
        result = trace_lineage(
            self.NODES, self.EDGES, "t", direction="downstream", max_depth=1
        )
        self.assertEqual(side_depths(result["downstream"]), {"t": 0, "a": 1, "c": 1})
        # No edge touches b, which is outside the depth bound.
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [("t", "a"), ("t", "c")],
        )

    def test_null_max_depth_is_unlimited(self):
        chain_nodes = ["n0", "n1", "n2", "n3", "n4"]
        chain_edges = [
            edge(chain_nodes[i], chain_nodes[i + 1]) for i in range(4)
        ]
        result = trace_lineage(
            chain_nodes, chain_edges, "n0", direction="downstream",
            max_depth=None,
        )
        self.assertEqual(
            side_ids(result["downstream"]), ["n0", "n1", "n2", "n3", "n4"]
        )


class CycleTest(unittest.TestCase):
    def test_self_loop_is_legal_and_terminates(self):
        nodes = ["t", "a"]
        edges = [edge("t", "t"), edge("t", "a"), edge("a", "a")]
        result = trace_lineage(nodes, edges, "t", direction="downstream")
        self.assertEqual(side_depths(result["downstream"]), {"t": 0, "a": 1})
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [("a", "a"), ("t", "a"), ("t", "t")],
        )

    def test_directed_cycle_is_legal_and_terminates(self):
        nodes = ["t", "a", "b"]
        edges = [edge("t", "a"), edge("a", "b"), edge("b", "t")]
        result = trace_lineage(nodes, edges, "t")
        self.assertEqual(
            side_depths(result["downstream"]), {"t": 0, "a": 1, "b": 2}
        )
        self.assertEqual(
            side_depths(result["upstream"]), {"t": 0, "b": 1, "a": 2}
        )
        self.assertEqual(
            side_edge_pairs(result["downstream"]),
            [("a", "b"), ("b", "t"), ("t", "a")],
        )


class InvalidGraphInputTest(unittest.TestCase):
    def assertInputError(self, nodes, edges):
        with self.assertRaises(InvalidLineageInputError):
            trace_lineage(nodes, edges, "t")
        with self.assertRaises(ValueError):
            trace_lineage(nodes, edges, "t")

    def test_nodes_missing_or_wrong_shape(self):
        self.assertInputError(None, [])
        self.assertInputError([], [])
        self.assertInputError("t", [])
        self.assertInputError({}, [])
        self.assertInputError(["t", "a", "a"], [])
        self.assertInputError(["t", ""], [])
        self.assertInputError(["t", 1], [])

    def test_edges_wrong_shape(self):
        self.assertInputError(["t"], None)
        self.assertInputError(["t"], {})
        self.assertInputError(["t"], [["t", "t"]])

    def test_edge_keys(self):
        self.assertInputError(["t"], [{"source": "t"}])
        self.assertInputError(["t"], [{"target": "t"}])
        self.assertInputError(["t"], [{}])
        self.assertInputError(
            ["t"], [{"source": "t", "target": "t", "weight": 1}]
        )

    def test_endpoints_must_be_declared_strings(self):
        self.assertInputError(["t"], [edge("t", "x")])
        self.assertInputError(["t"], [edge("x", "t")])
        self.assertInputError(["t"], [{"source": 1, "target": "t"}])
        self.assertInputError(["t"], [{"source": "t", "target": None}])

    def test_duplicate_edges_rejected_but_self_loop_allowed_once(self):
        self.assertInputError(
            ["a", "b"], [edge("a", "b"), edge("a", "b")]
        )
        # Opposite direction is a distinct edge.
        result = trace_lineage(
            ["a", "b"], [edge("a", "b"), edge("b", "a")], "a"
        )
        self.assertEqual(side_ids(result["downstream"]), ["a", "b"])


class InvalidQueryTest(unittest.TestCase):
    NODES = ["t", "a"]
    EDGES = [edge("t", "a")]

    def test_target_must_be_string(self):
        for bad in (None, 1, 1.0, True, ["t"], {"id": "t"}):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidLineageQueryError):
                    trace_lineage(self.NODES, self.EDGES, bad)
                with self.assertRaises(ValueError):
                    trace_lineage(self.NODES, self.EDGES, bad)

    def test_direction_enum(self):
        for bad in (None, "", "UPSTREAM", "both ", 1, True):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidLineageQueryError):
                    trace_lineage(self.NODES, self.EDGES, "t", direction=bad)

    def test_max_depth_rules(self):
        for bad in (-1, 1.0, "1", True, False, [0]):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidLineageQueryError):
                    trace_lineage(
                        self.NODES, self.EDGES, "t", max_depth=bad
                    )

    def test_undeclared_target(self):
        with self.assertRaises(UnknownLineageTargetError):
            trace_lineage(self.NODES, self.EDGES, "missing")
        with self.assertRaises(ValueError):
            trace_lineage(self.NODES, self.EDGES, "missing")

    def test_graph_errors_take_precedence_over_query(self):
        with self.assertRaises(InvalidLineageInputError):
            trace_lineage([], [], 123, direction="nope", max_depth=-1)

    def test_query_errors_take_precedence_over_unknown_target(self):
        with self.assertRaises(InvalidLineageQueryError):
            trace_lineage(
                self.NODES, self.EDGES, "missing", direction="sideways"
            )


if __name__ == "__main__":
    unittest.main()
