"""End-to-end tests for the ``dq snapshot-diff`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "snapshot-diff"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def result(rule_id, dataset_id, field_id, sample_id, violated=True, value=None):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
        "violated": violated,
        "value": value,
    }


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def path_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


LINEAGE = {
    "datasets": ["ods", "dwd"],
    "fields": {"ods": ["name"], "dwd": ["label"]},
    "edges": [edge("dwd", "label", "ods", "name", "upstream")],
}

BASE_PAYLOAD = {
    "baseline": {
        "results": [
            result("r2", "dwd", "label", "样本-1", value="bad"),
            result("r1", "ods", "name", "样本-1", value="x"),
        ],
    },
    "current": {
        "results": [
            result("r1", "ods", "name", "样本-1", value="y"),
            result("r3", "dwd", "label", "样本-2", value=0),
        ],
    },
    "lineage": LINEAGE,
}


class CliSnapshotDiffSuccessTest(unittest.TestCase):
    def test_diff_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "status": "ok",
                "summary": {
                    "baseline_violation_count": 2,
                    "current_violation_count": 2,
                    "new_count": 1,
                    "resolved_count": 1,
                    "persistent_count": 1,
                },
                "changes": [
                    {
                        "state": "persisted",
                        "rule_id": "r1",
                        "dataset_id": "ods",
                        "field_id": "name",
                        "sample_id": "样本-1",
                        "baseline_value": "x",
                        "current_value": "y",
                        "upstream_changes": [],
                        "paths": [],
                    },
                    {
                        "state": "resolved",
                        "rule_id": "r2",
                        "dataset_id": "dwd",
                        "field_id": "label",
                        "sample_id": "样本-1",
                        "baseline_value": "bad",
                        "current_value": None,
                        "upstream_changes": [
                            {
                                "rule_id": "r1",
                                "dataset_id": "ods",
                                "field_id": "name",
                                "sample_id": "样本-1",
                            }
                        ],
                        "paths": [
                            [
                                path_node("ods", "name"),
                                path_node("dwd", "label"),
                            ]
                        ],
                    },
                    {
                        "state": "new",
                        "rule_id": "r3",
                        "dataset_id": "dwd",
                        "field_id": "label",
                        "sample_id": "样本-2",
                        "baseline_value": None,
                        "current_value": 0,
                        "upstream_changes": [],
                        "paths": [],
                    },
                ],
            },
        )

    def test_single_line_of_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)

    def test_empty_snapshots(self):
        payload = {
            "baseline": {"results": []},
            "current": {"results": []},
            "lineage": LINEAGE,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["changes"], [])

    def test_nothing_written_to_stderr_on_success(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.stderr, b"")


class CliSnapshotDiffErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        # The error object is the only top-level content, on one line.
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"baseline": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_SNAPSHOT_INPUT")
        self._assert_error(run_json("snapshots"), "INVALID_SNAPSHOT_INPUT")

    def test_invalid_snapshot_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["lineage"]
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["baseline"]["results"]
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"][0]["rule_id"] = ""
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["lineage"]["edges"][0]["type"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["unexpected"] = True
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

    def test_unknown_snapshot_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_SNAPSHOT_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_SNAPSHOT_REFERENCE")

    def test_error_precedence(self):
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"][0]["dataset_id"] = "ghost"
        bad["lineage"]["edges"][0]["type"] = "bad"
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

    def test_nothing_written_to_stderr_on_error(self):
        proc = run_json([1])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, b"")


if __name__ == "__main__":
    unittest.main()
