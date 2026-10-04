"""End-to-end tests for the ``dq lineage-paths`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "lineage-paths"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class CliLineagePathsSuccessTest(unittest.TestCase):
    PAYLOAD = {
        "nodes": ["raw", "ods", "dwd", "ads"],
        "edges": [
            {"source": "raw", "target": "ods"},
            {"source": "ods", "target": "dwd"},
            {"source": "dwd", "target": "ads"},
        ],
        "target": "dwd",
    }

    def test_defaults_explain_both_sides(self):
        proc = run_json(self.PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], "dwd")
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        upstream_paths = [
            [step["node"]["id"] for step in path["nodes"]]
            for path in result["upstream"]["paths"]
        ]
        self.assertEqual(upstream_paths, [["dwd", "ods", "raw"]])
        downstream_paths = [
            [step["node"]["id"] for step in path["nodes"]]
            for path in result["downstream"]["paths"]
        ]
        self.assertEqual(downstream_paths, [["dwd", "ads"]])

        first_step = result["upstream"]["paths"][0]["nodes"][1]
        self.assertEqual(first_step["edge"], {"source": "ods", "target": "dwd"})
        self.assertIsNone(result["upstream"]["paths"][0]["nodes"][0]["edge"])
        self.assertFalse(result["upstream"]["cycle"])

    def test_no_relations_is_still_success_with_empty_paths(self):
        proc = run_json(
            {
                "nodes": ["raw", "ods"],
                "edges": [{"source": "raw", "target": "ods"}],
                "target": "ods",
                "direction": "downstream",
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            result["downstream"],
            {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []},
        )

    def test_cycle_result_is_success(self):
        payload = {
            "nodes": ["t", "a", "b"],
            "edges": [
                {"source": "t", "target": "a"},
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ],
            "target": "t",
            "direction": "downstream",
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        side = result["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], ["a", "b"])

    def test_max_depth_echoed(self):
        payload = dict(self.PAYLOAD, max_depth=0)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(
            [step["node"]["id"] for step in result["upstream"]["paths"][0]["nodes"]],
            ["dwd"],
        )


class CliLineagePathsErrorTest(unittest.TestCase):
    BASE = {
        "nodes": ["raw", "ods"],
        "edges": [{"source": "raw", "target": "ods"}],
        "target": "ods",
    }

    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_LINEAGE_INPUT")

    def test_invalid_graph_reuses_lineage_codes(self):
        self._assert_error(
            run_json({"edges": [], "target": "ods"}), "INVALID_LINEAGE_INPUT"
        )
        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [{"source": "raw", "target": "ghost"}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_INPUT")

    def test_invalid_query_reuses_lineage_codes(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")
        bad = json.loads(json.dumps(self.BASE))
        bad["max_depth"] = -2
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")

    def test_undeclared_target_uses_node_not_found_code(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = "ghost"
        self._assert_error(run_json(bad), "LINEAGE_NODE_NOT_FOUND")

    def test_error_precedence(self):
        payload = {
            "nodes": [],
            "edges": [],
            "target": "ghost",
            "direction": "sideways",
        }
        self._assert_error(run_json(payload), "INVALID_LINEAGE_INPUT")
        payload = {
            "nodes": ["a"],
            "edges": [],
            "target": "ghost",
            "direction": "sideways",
        }
        self._assert_error(run_json(payload), "INVALID_LINEAGE_QUERY")


if __name__ == "__main__":
    unittest.main()
