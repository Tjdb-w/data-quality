"""End-to-end tests for the ``dq field-lineage-paths`` CLI."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "field-lineage-paths"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def field(table, column):
    return {"table": table, "column": column}


PAYLOAD = {
    "fields": {
        "raw": ["name"],
        "ods": ["name"],
        "dwd": ["label"],
        "ads": ["label"],
    },
    "edges": [
        {"source": field("raw", "name"), "target": field("ods", "name")},
        {"source": field("ods", "name"), "target": field("dwd", "label")},
        {"source": field("dwd", "label"), "target": field("ads", "label")},
    ],
    "target": field("dwd", "label"),
}


class CliFieldLineagePathsSuccessTest(unittest.TestCase):
    def test_defaults_explain_both_sides(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], field("dwd", "label"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        def refs(path):
            return [
                (step["node"]["table"], step["node"]["column"])
                for step in path["nodes"]
            ]

        self.assertEqual(
            [refs(p) for p in result["upstream"]["paths"]],
            [[("dwd", "label"), ("ods", "name"), ("raw", "name")]],
        )
        self.assertEqual(
            [refs(p) for p in result["downstream"]["paths"]],
            [[("dwd", "label"), ("ads", "label")]],
        )
        self.assertIsNone(result["upstream"]["paths"][0]["nodes"][0]["edge"])
        self.assertEqual(
            result["upstream"]["paths"][0]["nodes"][1]["edge"],
            {"source": field("ods", "name"), "target": field("dwd", "label")},
        )

    def test_empty_relations_success(self):
        payload = json.loads(json.dumps(PAYLOAD))
        payload["target"] = field("ads", "label")
        payload["direction"] = "downstream"
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            result["downstream"],
            {"paths": [], "cycle": False, "cycle_nodes": [], "cycle_edges": []},
        )

    def test_cycle_success(self):
        payload = {
            "fields": {"t": ["f"], "a": ["f"], "b": ["f"]},
            "edges": [
                {"source": field("t", "f"), "target": field("a", "f")},
                {"source": field("a", "f"), "target": field("b", "f")},
                {"source": field("b", "f"), "target": field("a", "f")},
            ],
            "target": field("t", "f"),
            "direction": "downstream",
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        side = json.loads(proc.stdout.decode("utf-8"))["downstream"]
        self.assertTrue(side["cycle"])
        self.assertEqual(side["cycle_nodes"], [field("a", "f"), field("b", "f")])


class CliFieldLineagePathsErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{"), "INVALID_JSON")

    def test_invalid_graph_reuses_field_codes(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["fields"] = {"t": ["f"]}
        # Edges now reference undeclared ods/dwd fields.
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

    def test_invalid_query_reuses_field_codes(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

    def test_undeclared_target_uses_node_not_found_code(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["target"] = field("ads", "ghost")
        self._assert_error(run_json(bad), "LINEAGE_NODE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
