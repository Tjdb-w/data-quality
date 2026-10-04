"""Tests for :func:`data_quality.explain_lineage_paths`."""

import unittest

from data_quality import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    LineageNodeNotFoundError,
    explain_lineage_paths,
)


def edge(source, target):
    return {"source": source, "target": target}


def path_ids(path):
    return [step["node"]["id"] for step in path["nodes"]]


def path_edges(path):
    return [
        None if step["edge"] is None else (step["edge"]["source"], step["edge"]["target"])
        for step in path["nodes"]
    ]


def path_depths(path):
    return [step["node"]["depth"] for step in path["nodes"]]


EMPTY_SIDE = {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []}


class OrderedPathsTest(unittest.TestCase):
    # s -> a -> t
    # s -> b -> t        (two upstream source paths converging on t)
    NODES = ["t", "a", "b", "s", "iso"]
    EDGES = [edge("a", "t"), edge("b", "t"), edge("s", "a"), edge("s", "b")]

    def test_both_directions_echo_fields(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "t")
        self.assertEqual(result["target"], "t")
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

    def test_upstream_paths_are_ordered_target_to_source(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "t", direction="upstream")
        upstream = result["upstream"]
        self.assertFalse(upstream["cycle"])
        self.assertEqual(upstream["cycle_nodes"], [])
        self.assertEqual(upstream["cycle_edges"], [])
        self.assertEqual(
            [path_ids(p) for p in upstream["paths"]],
            [["t", "a", "s"], ["t", "b", "s"]],
        )
        # Each path reports its edge count and 0-based positions.
        self.assertEqual([p["depth"] for p in upstream["paths"]], [2, 2])
        for path in upstream["paths"]:
            self.assertEqual(path_depths(path), [0, 1, 2])
            self.assertEqual(
                path_edges(path),
                [None]
                + [(path_ids(path)[i], path_ids(path)[i - 1]) for i in range(1, 3)],
            )

    def test_same_node_can_appear_in_multiple_paths(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "t", direction="upstream")
        endpoints = [path_ids(p)[-1] for p in result["upstream"]["paths"]]
        self.assertEqual(endpoints.count("s"), 2)

    def test_downstream_from_source_reaches_both_branches(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "s", direction="downstream")
        self.assertEqual(
            sorted(path_ids(p) for p in result["downstream"]["paths"]),
            [["s", "a", "t"], ["s", "b", "t"]],
        )
        # A sink has no downstream relations: empty paths, not an error.
        sink = explain_lineage_paths(
            self.NODES, self.EDGES, "t", direction="downstream"
        )
        self.assertEqual(sink["downstream"], EMPTY_SIDE)

    def test_isolated_target_has_no_paths_either_side(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "iso")
        self.assertEqual(result["upstream"], EMPTY_SIDE)
        self.assertEqual(result["downstream"], EMPTY_SIDE)

    def test_one_side_leaves_other_side_empty_placeholder(self):
        result = explain_lineage_paths(
            self.NODES, self.EDGES, "t", direction="upstream"
        )
        self.assertEqual(result["downstream"], EMPTY_SIDE)
        self.assertTrue(result["upstream"]["paths"])


class DeterministicOrderTest(unittest.TestCase):
    def test_sorted_by_depth_then_node_ids_then_edge_ids(self):
        # t has parents a and z; a's source r1 is depth 2, z has no source.
        # Depth-1 parents sort a before z; a's longer path sorts after the
        # short one by depth.
        nodes = ["t", "a", "z", "r1"]
        edges = [edge("a", "t"), edge("z", "t"), edge("r1", "a")]
        result = explain_lineage_paths(nodes, edges, "t", direction="upstream")
        self.assertEqual(
            [path_ids(p) for p in result["upstream"]["paths"]],
            [["t", "z"], ["t", "a", "r1"]],
        )

    def test_result_independent_of_edge_declaration_order(self):
        nodes = ["t", "a", "b", "s"]
        base = [edge("a", "t"), edge("b", "t"), edge("s", "a"), edge("s", "b")]
        shuffled = [base[i] for i in (3, 0, 2, 1)]
        first = explain_lineage_paths(nodes, base, "t")
        second = explain_lineage_paths(nodes, shuffled, "t")
        self.assertEqual(first, second)

    def test_repeated_queries_are_identical(self):
        nodes = ["t", "a", "b"]
        edges = [edge("a", "t"), edge("b", "t"), edge("b", "a")]
        first = explain_lineage_paths(nodes, edges, "t")
        second = explain_lineage_paths(nodes, edges, "t")
        self.assertEqual(first, second)


class MaxDepthTest(unittest.TestCase):
    NODES = ["n0", "n1", "n2", "n3"]
    EDGES = [edge("n0", "n1"), edge("n1", "n2"), edge("n2", "n3")]

    def test_zero_returns_single_target_only_path(self):
        result = explain_lineage_paths(self.NODES, self.EDGES, "n1", max_depth=0)
        self.assertEqual(result["max_depth"], 0)
        for side in (result["upstream"], result["downstream"]):
            self.assertEqual(len(side["paths"]), 1)
            self.assertEqual(path_ids(side["paths"][0]), ["n1"])
            self.assertEqual(side["paths"][0]["depth"], 0)

    def test_depth_bound_truncates_longer_routes(self):
        result = explain_lineage_paths(
            self.NODES, self.EDGES, "n0", direction="downstream", max_depth=1
        )
        self.assertEqual(
            [path_ids(p) for p in result["downstream"]["paths"]],
            [["n0", "n1"]],
        )

    def test_null_is_unlimited(self):
        result = explain_lineage_paths(
            self.NODES, self.EDGES, "n0", direction="downstream", max_depth=None
        )
        self.assertEqual(
            [path_ids(p) for p in result["downstream"]["paths"]],
            [["n0", "n1", "n2", "n3"]],
        )


class CycleTest(unittest.TestCase):
    def test_directed_cycle_is_reported_without_losing_independent_paths(self):
        # t -> a -> b -> a closes a cycle, while t -> c is independent.
        nodes = ["t", "a", "b", "c"]
        edges = [edge("t", "a"), edge("a", "b"), edge("b", "a"), edge("t", "c")]
        result = explain_lineage_paths(
            nodes, edges, "t", direction="downstream"
        )
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], ["a", "b"])
        self.assertEqual(
            [(e["source"], e["target"]) for e in side["cycle_edges"]],
            [("a", "b"), ("b", "a")],
        )
        # The determined segment into the cycle and the independent branch
        # are both retained as simple paths.
        self.assertEqual(
            sorted(path_ids(p) for p in side["paths"]),
            [["t", "a", "b"], ["t", "c"]],
        )
        # No path repeats a node.
        for path in side["paths"]:
            ids = path_ids(path)
            self.assertEqual(len(ids), len(set(ids)))

    def test_cycle_reaching_back_to_target_is_reported(self):
        nodes = ["t", "a", "b"]
        edges = [edge("t", "a"), edge("a", "b"), edge("b", "t")]
        result = explain_lineage_paths(nodes, edges, "t", direction="downstream")
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], ["a", "b", "t"])
        self.assertEqual(
            [(e["source"], e["target"]) for e in side["cycle_edges"]],
            [("a", "b"), ("b", "t"), ("t", "a")],
        )
        self.assertEqual(path_ids(side["paths"][0]), ["t", "a", "b"])

    def test_self_loop_is_a_cycle_but_sibling_path_is_kept(self):
        nodes = ["t", "a"]
        edges = [edge("t", "t"), edge("t", "a")]
        result = explain_lineage_paths(nodes, edges, "t", direction="downstream")
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], ["t"])
        self.assertEqual(side["cycle_edges"], [edge("t", "t")])
        self.assertEqual(
            [path_ids(p) for p in side["paths"]],
            [["t", "a"]],
        )

    def test_acyclic_graph_never_flags_cycle(self):
        nodes = ["t", "a", "b"]
        edges = [edge("a", "t"), edge("b", "a")]
        result = explain_lineage_paths(nodes, edges, "t", direction="upstream")
        self.assertFalse(result["upstream"]["cycle"])
        self.assertEqual(result["upstream"]["cycle_nodes"], [])
        self.assertEqual(result["upstream"]["cycle_edges"], [])


class ErrorTest(unittest.TestCase):
    NODES = ["t", "a"]
    EDGES = [edge("t", "a")]

    def test_undeclared_target_raises_node_not_found(self):
        with self.assertRaises(LineageNodeNotFoundError) as ctx:
            explain_lineage_paths(self.NODES, self.EDGES, "ghost")
        self.assertEqual(ctx.exception.code, "LINEAGE_NODE_NOT_FOUND")
        self.assertIsInstance(ctx.exception, ValueError)

    def test_graph_errors_reuse_existing_exception(self):
        with self.assertRaises(InvalidLineageInputError):
            explain_lineage_paths([], [], "t")
        with self.assertRaises(InvalidLineageInputError):
            explain_lineage_paths(
                ["a", "b"], [edge("a", "b"), edge("a", "b")], "a"
            )
        with self.assertRaises(InvalidLineageInputError):
            explain_lineage_paths(["t"], [edge("t", "x")], "t")

    def test_query_errors_reuse_existing_exception(self):
        with self.assertRaises(InvalidLineageQueryError):
            explain_lineage_paths(self.NODES, self.EDGES, 5)
        with self.assertRaises(InvalidLineageQueryError):
            explain_lineage_paths(self.NODES, self.EDGES, "t", direction="sideways")
        with self.assertRaises(InvalidLineageQueryError):
            explain_lineage_paths(self.NODES, self.EDGES, "t", max_depth=-1)
        with self.assertRaises(InvalidLineageQueryError):
            explain_lineage_paths(self.NODES, self.EDGES, "t", max_depth=True)

    def test_validation_order_graph_query_target(self):
        with self.assertRaises(InvalidLineageInputError):
            explain_lineage_paths([], [], "ghost", direction="nope")
        with self.assertRaises(InvalidLineageQueryError):
            explain_lineage_paths(
                ["t"], [], "ghost", direction="sideways"
            )


if __name__ == "__main__":
    unittest.main()
