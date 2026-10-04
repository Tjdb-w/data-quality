"""Unit tests for ``data_quality.lineage_paths.explain_lineage_paths``."""

import unittest

from data_quality import (
    InvalidLineagePathInputError,
    InvalidLineagePathQueryError,
    explain_lineage_paths,
)


def make_graph(**overrides):
    graph = {
        "tables": ["raw", "ods", "dwd", "log", "ads"],
        "processes": ["etl"],
        "fields": {"ods": ["name"], "dwd": ["label"]},
        "edges": [
            {"source": {"table": "raw"}, "target": {"table": "ods"},
             "type": "ingest"},
            {"source": {"table": "ods"}, "target": {"table": "dwd"},
             "type": "load"},
            {"source": {"table": "log"}, "target": {"table": "dwd"},
             "type": "load"},
            {"source": {"table": "ods", "field": "name"},
             "target": {"table": "dwd", "field": "label"}, "type": "map"},
            {"source": {"table": "dwd"}, "target": {"process": "etl"},
             "type": "read"},
            {"source": {"process": "etl"}, "target": {"table": "ads"},
             "type": "write"},
        ],
    }
    graph.update(overrides)
    return graph


class UpstreamPathTest(unittest.TestCase):
    def test_multiple_paths_ordered_by_depth_then_node_ids(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "dwd"}, direction="upstream"
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["target"], {"table": "dwd"})
        self.assertEqual(result["direction"], "upstream")

        paths = result["paths"]
        self.assertEqual(len(paths), 2)

        first, second = paths
        self.assertEqual(first["direction"], "upstream")
        self.assertFalse(first["cycle"])
        self.assertEqual(first["cycle_nodes"], [])
        self.assertIsNone(first["cycle_edge"])
        self.assertEqual(first["depth"], 1)
        self.assertEqual(
            [step["node"]["id"] for step in first["steps"]],
            ["table:dwd", "table:log"],
        )
        self.assertEqual(second["depth"], 2)
        self.assertEqual(
            [step["node"]["id"] for step in second["steps"]],
            ["table:dwd", "table:ods", "table:raw"],
        )

    def test_steps_carry_position_node_and_direct_edge(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "dwd"}, direction="upstream"
        )
        path = result["paths"][1]
        steps = path["steps"]

        self.assertEqual(steps[0]["position"], 0)
        self.assertIsNone(steps[0]["edge"])
        self.assertEqual(
            steps[0]["node"],
            {"id": "table:dwd", "type": "table",
             "table": "dwd", "field": None},
        )

        self.assertEqual(steps[1]["position"], 1)
        self.assertEqual(
            steps[1]["edge"],
            {"id": "table:ods->table:dwd", "type": "load",
             "source": "table:ods", "target": "table:dwd"},
        )
        self.assertEqual(steps[2]["position"], 2)
        self.assertEqual(steps[2]["edge"]["id"], "table:raw->table:ods")

    def test_field_level_target(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "dwd", "field": "label"},
            direction="upstream",
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            result["target"], {"table": "dwd", "field": "label"}
        )
        self.assertEqual(len(result["paths"]), 1)
        steps = result["paths"][0]["steps"]
        self.assertEqual(
            [step["node"]["id"] for step in steps],
            ["field:dwd.label", "field:ods.name"],
        )
        self.assertEqual(
            steps[1]["node"],
            {"id": "field:ods.name", "type": "field",
             "table": "ods", "field": "name"},
        )
        self.assertEqual(steps[1]["edge"]["type"], "map")


class DownstreamImpactTest(unittest.TestCase):
    def test_impact_scope_through_process_nodes(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "dwd"}, direction="downstream"
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["paths"]), 1)
        path = result["paths"][0]
        self.assertEqual(path["direction"], "downstream")
        self.assertEqual(path["depth"], 2)
        self.assertEqual(
            [step["node"]["id"] for step in path["steps"]],
            ["table:dwd", "process:etl", "table:ads"],
        )
        process_step = path["steps"][1]
        self.assertEqual(
            process_step["node"],
            {"id": "process:etl", "type": "process",
             "table": None, "field": None},
        )

    def test_both_directions_grouped_upstream_first(self):
        result = explain_lineage_paths(make_graph(), {"table": "dwd"})
        self.assertEqual(result["direction"], "both")
        directions = [path["direction"] for path in result["paths"]]
        self.assertEqual(directions, ["upstream", "upstream", "downstream"])


class EmptyAndMissingTargetTest(unittest.TestCase):
    def test_declared_target_without_relations_returns_empty_paths(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "ads"}, direction="downstream"
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["paths"], [])

    def test_declared_target_without_any_relation_both_directions(self):
        graph = make_graph(tables=["raw", "ods", "dwd", "log", "ads", "iso"])
        result = explain_lineage_paths(graph, {"table": "iso"})
        self.assertTrue(result["success"])
        self.assertEqual(result["paths"], [])

    def test_unknown_target_returns_error_result_not_exception(self):
        result = explain_lineage_paths(make_graph(), {"table": "missing"})
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "LINEAGE_NODE_NOT_FOUND")
        self.assertTrue(result["message"])
        self.assertEqual(result["target"], {"table": "missing"})

    def test_unknown_field_target_returns_same_error_code(self):
        result = explain_lineage_paths(
            make_graph(), {"table": "dwd", "field": "nope"},
            direction="downstream",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "LINEAGE_NODE_NOT_FOUND")


class CycleTest(unittest.TestCase):
    GRAPH = {
        "tables": ["a", "b", "c", "d"],
        "processes": [],
        "fields": {},
        "edges": [
            {"source": {"table": "a"}, "target": {"table": "b"},
             "type": "e"},
            {"source": {"table": "b"}, "target": {"table": "c"},
             "type": "e"},
            {"source": {"table": "c"}, "target": {"table": "b"},
             "type": "e"},
            {"source": {"table": "b"}, "target": {"table": "d"},
             "type": "e"},
        ],
    }

    def test_cycle_path_keeps_segment_and_cycle_nodes(self):
        result = explain_lineage_paths(
            self.GRAPH, {"table": "a"}, direction="downstream"
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["paths"]), 2)

        cycle_path, plain_path = result["paths"]
        self.assertTrue(cycle_path["cycle"])
        self.assertEqual(
            [step["node"]["id"] for step in cycle_path["steps"]],
            ["table:a", "table:b", "table:c"],
        )
        self.assertEqual(
            [node["id"] for node in cycle_path["cycle_nodes"]],
            ["table:b", "table:c"],
        )
        self.assertEqual(cycle_path["cycle_edge"]["id"], "table:c->table:b")

        # The independent path through the same cycle node is preserved.
        self.assertFalse(plain_path["cycle"])
        self.assertEqual(plain_path["cycle_nodes"], [])
        self.assertIsNone(plain_path["cycle_edge"])
        self.assertEqual(
            [step["node"]["id"] for step in plain_path["steps"]],
            ["table:a", "table:b", "table:d"],
        )

    def test_self_loop_is_a_cycle_path(self):
        graph = {
            "tables": ["a"],
            "processes": [],
            "fields": {},
            "edges": [
                {"source": {"table": "a"}, "target": {"table": "a"},
                 "type": "loop"},
            ],
        }
        result = explain_lineage_paths(
            graph, {"table": "a"}, direction="downstream"
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["paths"]), 1)
        path = result["paths"][0]
        self.assertTrue(path["cycle"])
        self.assertEqual(path["depth"], 0)
        self.assertEqual(
            [node["id"] for node in path["cycle_nodes"]], ["table:a"]
        )
        self.assertEqual(path["cycle_edge"]["id"], "table:a->table:a")

    def test_upstream_cycle_does_not_revisit_nodes(self):
        result = explain_lineage_paths(
            self.GRAPH, {"table": "d"}, direction="upstream"
        )
        self.assertTrue(result["success"])
        for path in result["paths"]:
            ids = [step["node"]["id"] for step in path["steps"]]
            self.assertEqual(len(ids), len(set(ids)))


class DeterminismTest(unittest.TestCase):
    def test_repeated_queries_are_identical(self):
        first = explain_lineage_paths(make_graph(), {"table": "dwd"})
        second = explain_lineage_paths(make_graph(), {"table": "dwd"})
        self.assertEqual(first, second)

    def test_edge_declaration_order_does_not_change_results(self):
        graph = make_graph()
        reordered = make_graph(edges=list(reversed(graph["edges"])))
        self.assertEqual(
            explain_lineage_paths(graph, {"table": "dwd"}),
            explain_lineage_paths(reordered, {"table": "dwd"}),
        )


class InputValidationTest(unittest.TestCase):
    def assert_input_error(self, graph, target=None):
        with self.assertRaises(InvalidLineagePathInputError):
            explain_lineage_paths(
                graph, target if target is not None else {"table": "dwd"}
            )

    def test_graph_must_be_an_object(self):
        self.assert_input_error([])

    def test_graph_missing_keys(self):
        self.assert_input_error({"tables": [], "processes": [], "fields": {}})

    def test_graph_unsupported_keys(self):
        graph = make_graph(extra=True)
        self.assert_input_error(graph)

    def test_duplicate_table(self):
        self.assert_input_error(make_graph(tables=["dwd", "dwd"]))

    def test_fields_for_undeclared_table(self):
        self.assert_input_error(make_graph(fields={"ghost": ["c"]}))

    def test_duplicate_edge_rejected(self):
        graph = make_graph()
        graph["edges"] = graph["edges"] + [graph["edges"][0]]
        self.assert_input_error(graph)

    def test_dangling_edge_endpoint(self):
        graph = make_graph(
            edges=[{"source": {"table": "ghost"},
                    "target": {"table": "dwd"}, "type": "e"}]
        )
        self.assert_input_error(graph)

    def test_edge_type_must_be_a_non_empty_string(self):
        graph = make_graph(
            edges=[{"source": {"table": "raw"},
                    "target": {"table": "ods"}, "type": ""}]
        )
        self.assert_input_error(graph)


class QueryValidationTest(unittest.TestCase):
    def test_bad_direction(self):
        with self.assertRaises(InvalidLineagePathQueryError):
            explain_lineage_paths(
                make_graph(), {"table": "dwd"}, direction="sideways"
            )

    def test_target_must_be_a_node_reference(self):
        with self.assertRaises(InvalidLineagePathQueryError):
            explain_lineage_paths(make_graph(), "dwd")

    def test_target_with_unknown_keys(self):
        with self.assertRaises(InvalidLineagePathQueryError):
            explain_lineage_paths(make_graph(), {"node": "dwd"})

    def test_target_with_empty_name(self):
        with self.assertRaises(InvalidLineagePathQueryError):
            explain_lineage_paths(make_graph(), {"table": ""})


if __name__ == "__main__":
    unittest.main()
