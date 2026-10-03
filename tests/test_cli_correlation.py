"""End-to-end tests for the ``dq correlate-anomalies`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "correlate-anomalies"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def field(dataset, field_id):
    return {"dataset_id": dataset, "field_id": field_id}


def edge(source, target, edge_type):
    return {"source": source, "target": target, "type": edge_type}


def result(rule_id, dataset, field_id, sample_id, is_violation, value):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset,
        "field_id": field_id,
        "sample_id": sample_id,
        "is_violation": is_violation,
        "violating_value": value,
    }


GRAPH = {
    "nodes": [
        field("raw", "name"),
        field("ods", "name"),
        field("dwd", "label"),
        field("ads", "label"),
    ],
    "edges": [
        edge(field("raw", "name"), field("ods", "name"), "downstream"),
        edge(field("ods", "name"), field("dwd", "label"), "downstream"),
        edge(field("dwd", "label"), field("ads", "label"), "downstream"),
    ],
}


class CliCorrelationSuccessTest(unittest.TestCase):
    def test_correlated_events(self):
        payload = {
            "results": [
                result("r2", "ods", "name", "s1", True, "y"),
                result("r1", "raw", "name", "s1", True, "x"),
            ],
            "lineage_graph": GRAPH,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            out,
            {
                "events": [
                    {
                        "sample_id": "s1",
                        "rule_ids": ["r1", "r2"],
                        "fields": [
                            field("ods", "name"),
                            field("raw", "name"),
                        ],
                        "upstream_fields": [],
                        "downstream_fields": [field("dwd", "label")],
                    }
                ]
            },
        )

    def test_empty_inputs(self):
        proc = run_json({"results": [], "lineage_graph": {"nodes": [], "edges": []}})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")), {"events": []}
        )

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_JSON")

    def test_payload_must_be_object(self):
        proc = run_json([1, 2])
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_CORRELATION_INPUT")

    def test_missing_results(self):
        proc = run_json({"lineage_graph": GRAPH})
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_CORRELATION_INPUT")

    def test_missing_lineage_graph(self):
        proc = run_json({"results": []})
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_CORRELATION_GRAPH")

    def test_bad_result_structure(self):
        payload = {
            "results": [result("", "d", "f", "s", True, None)],
            "lineage_graph": {"nodes": [field("d", "f")], "edges": []},
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_CORRELATION_INPUT")

    def test_bad_graph_structure(self):
        payload = {
            "results": [],
            "lineage_graph": {
                "nodes": [field("d", "a")],
                "edges": [edge(field("d", "a"), field("d", "b"), "sideways")],
            },
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "INVALID_CORRELATION_GRAPH")

    def test_conflicting_duplicate_result(self):
        payload = {
            "results": [
                result("r", "d", "a", "s", True, 1),
                result("r", "d", "a", "s", True, 2),
            ],
            "lineage_graph": {"nodes": [field("d", "a")], "edges": []},
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "CONFLICTING_CORRELATION_RESULT")

    def test_unknown_reference(self):
        payload = {
            "results": [result("r", "ghost", "a", "s", True, 1)],
            "lineage_graph": {"nodes": [field("d", "a")], "edges": []},
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stdout.decode("utf-8"))["error"]
        self.assertEqual(error["code"], "UNKNOWN_CORRELATION_REFERENCE")


if __name__ == "__main__":
    unittest.main()
