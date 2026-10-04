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


PAYLOAD = {
    "tables": ["raw", "ods", "dwd", "ads"],
    "processes": ["etl"],
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [
        {"source": {"table": "raw"}, "target": {"table": "ods"},
         "type": "ingest"},
        {"source": {"table": "ods"}, "target": {"table": "dwd"},
         "type": "load"},
        {"source": {"table": "dwd"}, "target": {"process": "etl"},
         "type": "read"},
        {"source": {"process": "etl"}, "target": {"table": "ads"},
         "type": "write"},
    ],
    "target": {"table": "dwd"},
}


class CliLineagePathsSuccessTest(unittest.TestCase):
    def test_upstream_paths(self):
        payload = dict(PAYLOAD, direction="upstream")
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(result["success"])
        self.assertEqual(result["target"], {"table": "dwd"})
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(len(result["paths"]), 1)
        path = result["paths"][0]
        self.assertEqual(
            [step["node"]["id"] for step in path["steps"]],
            ["table:dwd", "table:ods", "table:raw"],
        )
        self.assertFalse(path["cycle"])

    def test_direction_defaults_to_both(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "both")
        directions = [path["direction"] for path in result["paths"]]
        self.assertEqual(directions, ["upstream", "downstream"])

    def test_declared_target_without_relations(self):
        payload = dict(PAYLOAD, target={"table": "ads"}, direction="downstream")
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(result["success"])
        self.assertEqual(result["paths"], [])


class CliLineagePathsErrorTest(unittest.TestCase):
    def test_unknown_target(self):
        payload = dict(PAYLOAD, target={"table": "missing"})
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "LINEAGE_NODE_NOT_FOUND")
        self.assertTrue(body["message"])

    def test_invalid_graph(self):
        payload = dict(PAYLOAD, edges=[{"source": {"table": "ghost"},
                                       "target": {"table": "dwd"},
                                       "type": "e"}])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_LINEAGE_PATH_INPUT")
        self.assertTrue(body["error"]["message"])

    def test_invalid_query(self):
        payload = dict(PAYLOAD, direction="sideways")
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_LINEAGE_PATH_QUERY")

    def test_non_object_payload(self):
        proc = run_cli(b"[]")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_LINEAGE_PATH_INPUT")

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_JSON")


if __name__ == "__main__":
    unittest.main()
