"""Tests for data_quality.trace_lineage and the ``dq lineage`` CLI."""

import json
import subprocess
import sys
import unittest

from data_quality import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    UnknownLineageTargetError,
    trace_lineage,
)

NODES = ["a", "b", "c", "d", "e"]
EDGES = [
    {"source": "a", "target": "b"},
    {"source": "b", "target": "c"},
    {"source": "a", "target": "c"},
    {"source": "c", "target": "d"},
]


def edge(source, target):
    return {"source": source, "target": target}


class TraceBothTest(unittest.TestCase):
    def test_both_directions(self):
        result = trace_lineage(NODES, EDGES, "c")
        self.assertEqual(result["target"], "c")
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        # Shortest depth wins: a reaches c directly (1), not via b (2).
        self.assertEqual(
            result["upstream"]["nodes"],
            [
                {"id": "c", "depth": 0},
                {"id": "a", "depth": 1},
                {"id": "b", "depth": 1},
            ],
        )
        self.assertEqual(
            result["upstream"]["edges"],
            [edge("a", "b"), edge("a", "c"), edge("b", "c")],
        )
        self.assertEqual(
            result["downstream"]["nodes"],
            [{"id": "c", "depth": 0}, {"id": "d", "depth": 1}],
        )
        self.assertEqual(result["downstream"]["edges"], [edge("c", "d")])

    def test_single_side_leaves_other_empty(self):
        result = trace_lineage(NODES, EDGES, "c", direction="downstream")
        self.assertEqual(result["direction"], "downstream")
        self.assertEqual(result["upstream"], {"nodes": [], "edges": []})
        self.assertEqual(
            [n["id"] for n in result["downstream"]["nodes"]], ["c", "d"]
        )

        result = trace_lineage(NODES, EDGES, "c", direction="upstream")
        self.assertEqual(result["downstream"], {"nodes": [], "edges": []})
        self.assertEqual(
            [n["id"] for n in result["upstream"]["nodes"]], ["c", "a", "b"]
        )

    def test_no_relatives(self):
        result = trace_lineage(NODES, EDGES, "e")
        self.assertEqual(
            result["upstream"],
            {"nodes": [{"id": "e", "depth": 0}], "edges": []},
        )
        self.assertEqual(
            result["downstream"],
            {"nodes": [{"id": "e", "depth": 0}], "edges": []},
        )

    def test_max_depth_zero_contains_only_target(self):
        result = trace_lineage(NODES, EDGES, "c", max_depth=0)
        self.assertEqual(result["max_depth"], 0)
        for side in ("upstream", "downstream"):
            self.assertEqual(
                result[side],
                {"nodes": [{"id": "c", "depth": 0}], "edges": []},
            )

    def test_max_depth_limits_expansion(self):
        result = trace_lineage(NODES, EDGES, "a", direction="downstream",
                               max_depth=1)
        self.assertEqual(
            result["downstream"]["nodes"],
            [
                {"id": "a", "depth": 0},
                {"id": "b", "depth": 1},
                {"id": "c", "depth": 1},
            ],
        )
        # Edges are all original edges between included nodes, so b->c
        # (both endpoints at depth 1) is included even though the
        # depth-limited walk never traverses it.
        self.assertEqual(
            result["downstream"]["edges"],
            [edge("a", "b"), edge("a", "c"), edge("b", "c")],
        )

    def test_cycles_and_self_loops_terminate(self):
        nodes = ["x", "y", "z"]
        edges = [edge("x", "y"), edge("y", "z"), edge("z", "x"),
                 edge("y", "y")]
        result = trace_lineage(nodes, edges, "x", direction="downstream")
        self.assertEqual(
            result["downstream"]["nodes"],
            [
                {"id": "x", "depth": 0},
                {"id": "y", "depth": 1},
                {"id": "z", "depth": 2},
            ],
        )
        self.assertEqual(
            result["downstream"]["edges"],
            [edge("x", "y"), edge("y", "y"), edge("y", "z"), edge("z", "x")],
        )

    def test_depth_sorting_then_id(self):
        nodes = ["t", "b2", "b1", "a"]
        edges = [edge("t", "b2"), edge("t", "b1"), edge("b1", "a")]
        result = trace_lineage(nodes, edges, "t", direction="downstream")
        self.assertEqual(
            result["downstream"]["nodes"],
            [
                {"id": "t", "depth": 0},
                {"id": "b1", "depth": 1},
                {"id": "b2", "depth": 1},
                {"id": "a", "depth": 2},
            ],
        )


class LineageInputErrorTest(unittest.TestCase):
    def assert_input_error(self, nodes, edges):
        with self.assertRaises(InvalidLineageInputError):
            trace_lineage(nodes, edges, "a")

    def test_errors_are_value_errors(self):
        for exc in (
            InvalidLineageInputError,
            InvalidLineageQueryError,
            UnknownLineageTargetError,
        ):
            self.assertTrue(issubclass(exc, ValueError))

    def test_nodes_missing_or_empty(self):
        self.assert_input_error(None, [])
        self.assert_input_error([], [])

    def test_nodes_must_be_unique_strings(self):
        self.assert_input_error(["a", "a"], [])
        self.assert_input_error(["a", 1], [])
        self.assert_input_error(["a", ""], [])

    def test_edges_structure(self):
        self.assert_input_error(["a"], "not-a-list")
        self.assert_input_error(["a"], [1])
        self.assert_input_error(["a"], [{"source": "a"}])
        self.assert_input_error(["a"], [{"source": "a", "target": "a",
                                         "extra": 1}])
        self.assert_input_error(["a"], [edge("a", 1)])

    def test_edge_endpoints_must_be_declared(self):
        self.assert_input_error(["a"], [edge("a", "ghost")])
        self.assert_input_error(["a"], [edge("ghost", "a")])

    def test_duplicate_edges_rejected(self):
        self.assert_input_error(["a", "b"], [edge("a", "b"), edge("a", "b")])
        # Reversed direction is a different edge.
        trace_lineage(["a", "b"], [edge("a", "b"), edge("b", "a")], "a")


class LineageQueryErrorTest(unittest.TestCase):
    def test_bad_direction(self):
        for bad in ("sideways", "", None, 1, ["both"]):
            with self.assertRaises(InvalidLineageQueryError):
                trace_lineage(NODES, EDGES, "c", direction=bad)

    def test_bad_max_depth(self):
        for bad in (-1, 1.5, "1", True, False):
            with self.assertRaises(InvalidLineageQueryError):
                trace_lineage(NODES, EDGES, "c", max_depth=bad)

    def test_bad_target_type(self):
        for bad in (None, 1, ["c"], {"id": "c"}):
            with self.assertRaises(InvalidLineageQueryError):
                trace_lineage(NODES, EDGES, bad)

    def test_unknown_target(self):
        with self.assertRaises(UnknownLineageTargetError):
            trace_lineage(NODES, EDGES, "ghost")


PKG = [sys.executable, "-m", "data_quality", "lineage"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class LineageCliTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_success_with_defaults(self):
        proc = run_json({"nodes": NODES, "edges": EDGES, "target": "c"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])
        self.assertEqual(
            [n["id"] for n in result["upstream"]["nodes"]], ["c", "a", "b"]
        )

    def test_success_with_utf8_and_options(self):
        proc = run_json(
            {
                "nodes": ["源", "中间", "末端"],
                "edges": [edge("源", "中间"), edge("中间", "末端")],
                "target": "中间",
                "direction": "upstream",
                "max_depth": 1,
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            result["upstream"]["nodes"],
            [{"id": "中间", "depth": 0}, {"id": "源", "depth": 1}],
        )
        self.assertEqual(result["downstream"], {"nodes": [], "edges": []})
        self.assertIn("中间", proc.stdout.decode("utf-8"))

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{not json"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"nodes": [\xff]}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_LINEAGE_INPUT")

    def test_missing_or_bad_graph(self):
        self._assert_error(run_json({"edges": [], "target": "a"}),
                           "INVALID_LINEAGE_INPUT")
        self._assert_error(
            run_json({"nodes": ["a"], "edges": [edge("a", "b")],
                      "target": "a"}),
            "INVALID_LINEAGE_INPUT",
        )
        self._assert_error(
            run_json({"nodes": ["a", "b"],
                      "edges": [edge("a", "b"), edge("a", "b")],
                      "target": "a"}),
            "INVALID_LINEAGE_INPUT",
        )

    def test_bad_query(self):
        self._assert_error(
            run_json({"nodes": NODES, "edges": EDGES, "target": "c",
                      "direction": "sideways"}),
            "INVALID_LINEAGE_QUERY",
        )
        self._assert_error(
            run_json({"nodes": NODES, "edges": EDGES, "target": "c",
                      "max_depth": True}),
            "INVALID_LINEAGE_QUERY",
        )
        self._assert_error(
            run_json({"nodes": NODES, "edges": EDGES}),
            "INVALID_LINEAGE_QUERY",
        )

    def test_unknown_target(self):
        self._assert_error(
            run_json({"nodes": NODES, "edges": EDGES, "target": "ghost"}),
            "UNKNOWN_LINEAGE_TARGET",
        )


if __name__ == "__main__":
    unittest.main()
