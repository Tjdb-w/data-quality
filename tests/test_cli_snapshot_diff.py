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


def field_ref(dataset_id, field_id):
    return {"dataset_id": dataset_id, "field_id": field_id}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


BASE_PAYLOAD = {
    "baseline": {
        "results": [
            result("r1", "ods", "name", "样本-1", value="old"),
            result("r2", "ads", "label", "样本-1", value="was-bad"),
        ]
    },
    "current": {
        "results": [
            result("r1", "ods", "name", "样本-1", value="new"),
            result("r3", "dwd", "label", "样本-1", value="now-bad"),
        ]
    },
    "lineage": {
        "datasets": ["ods", "dwd", "ads"],
        "fields": {"ods": ["name"], "dwd": ["label"], "ads": ["label"]},
        "edges": [
            edge("dwd", "label", "ods", "name", "upstream"),
            edge("dwd", "label", "ads", "label", "downstream"),
        ],
    },
}


class CliSnapshotDiffSuccessTest(unittest.TestCase):
    def test_full_report(self):
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
                        "baseline_value": "old",
                        "current_value": "new",
                        "upstream_changes": [],
                        "paths": [],
                    },
                    {
                        "state": "resolved",
                        "rule_id": "r2",
                        "dataset_id": "ads",
                        "field_id": "label",
                        "sample_id": "样本-1",
                        "baseline_value": "was-bad",
                        "current_value": None,
                        "upstream_changes": [
                            {
                                "rule_id": "r3",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-1",
                            },
                            {
                                "rule_id": "r1",
                                "dataset_id": "ods",
                                "field_id": "name",
                                "sample_id": "样本-1",
                            },
                        ],
                        "paths": [
                            [
                                field_ref("dwd", "label"),
                                field_ref("ads", "label"),
                            ],
                            [
                                field_ref("ods", "name"),
                                field_ref("dwd", "label"),
                                field_ref("ads", "label"),
                            ],
                        ],
                    },
                    {
                        "state": "new",
                        "rule_id": "r3",
                        "dataset_id": "dwd",
                        "field_id": "label",
                        "sample_id": "样本-1",
                        "baseline_value": None,
                        "current_value": "now-bad",
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
                                field_ref("ods", "name"),
                                field_ref("dwd", "label"),
                            ]
                        ],
                    },
                ],
            },
        )

    def test_output_is_a_single_json_line(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        raw = proc.stdout.decode("utf-8")
        self.assertEqual(raw.count("\n"), 1)
        self.assertTrue(raw.endswith("\n"))
        json.loads(raw)

    def test_empty_snapshots(self):
        payload = json.loads(json.dumps(BASE_PAYLOAD))
        payload["baseline"]["results"] = []
        payload["current"]["results"] = []
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            {
                "status": "ok",
                "summary": {
                    "baseline_violation_count": 0,
                    "current_violation_count": 0,
                    "new_count": 0,
                    "resolved_count": 0,
                    "persistent_count": 0,
                },
                "changes": [],
            },
        )

    def test_utf8_round_trip(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("样本-1".encode("utf-8"), proc.stdout)


class CliSnapshotDiffErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        # A single JSON object line, nothing on stdout besides it.
        self.assertEqual(proc.stdout.decode("utf-8").count("\n"), 1)

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"baseline": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_SNAPSHOT_INPUT")
        self._assert_error(run_json("snapshots"), "INVALID_SNAPSHOT_INPUT")

    def test_invalid_snapshot_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["baseline"]
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["current"]
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["extra"] = []
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"] = "nope"
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"][0]["violated"] = "yes"
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["extra"] = {}
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")

    def test_unknown_snapshot_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_SNAPSHOT_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_SNAPSHOT_REFERENCE")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed structure beats unknown references.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"] = "nope"
        bad["baseline"]["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "INVALID_SNAPSHOT_INPUT")


if __name__ == "__main__":
    unittest.main()
