"""End-to-end tests for the ``dq quality-gates`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "quality-gates"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


PAYLOAD = {
    "dataset": "dwd_orders",
    "records": [
        {"id": "r1", "age": 5},
        {"id": "r2", "age": 200},
        {"id": "r3", "age": None},
        {"age": 10},
    ],
    "rules": [
        {"id": "age-req", "type": "required", "options": {"field": "age"}},
        {
            "id": "age-range",
            "type": "range",
            "options": {"field": "age", "min": 0, "max": 120},
        },
    ],
    "gates": [
        {"rule_id": "g-passed", "source_rule_id": "age-req",
         "max_failed_ratio": 0.25, "severity": "error"},
        {"rule_id": "g-failed", "source_rule_id": "age-range",
         "max_failed_ratio": 0.1, "severity": "warning"},
    ],
}


class CliQualityGatesSuccessTest(unittest.TestCase):
    def test_report_output(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "dataset": "dwd_orders",
                "record_count": 4,
                "results": [
                    {
                        "rule_id": "g-passed",
                        "source_rule_id": "age-req",
                        "status": "PASSED",
                        "failed_count": 1,
                        "record_count": 4,
                        "ratio": 0.25,
                        "samples": [
                            {
                                "record_index": 2,
                                "record_id": "r3",
                                "field": "age",
                                "value": None,
                                "message": "is required but is missing "
                                           "or null",
                            }
                        ],
                    },
                    {
                        "rule_id": "g-failed",
                        "source_rule_id": "age-range",
                        "status": "FAILED",
                        "failed_count": 2,
                        "record_count": 4,
                        "ratio": 0.5,
                        "samples": [
                            {
                                "record_index": 1,
                                "record_id": "r2",
                                "field": "age",
                                "value": 200,
                                "message": "is out of the allowed range",
                            },
                            {
                                "record_index": 2,
                                "record_id": "r3",
                                "field": "age",
                                "value": None,
                                "message": "is out of the allowed range",
                            },
                        ],
                    },
                ],
            },
        )

    def test_failed_gate_still_exits_zero(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_output_is_a_single_line(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)

    def test_empty_dataset_skipped_and_exits_zero(self):
        payload = {
            "dataset": "dwd_orders",
            "records": [],
            "rules": PAYLOAD["rules"],
            "gates": [
                {"rule_id": "g1", "source_rule_id": "age-req",
                 "max_failed_ratio": 0.0, "severity": "error"}
            ],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["record_count"], 0)
        self.assertEqual(
            body["results"],
            [
                {
                    "rule_id": "g1",
                    "source_rule_id": "age-req",
                    "status": "SKIPPED_EMPTY_DATASET",
                    "failed_count": 0,
                    "record_count": 0,
                    "ratio": None,
                    "samples": [],
                }
            ],
        )

    def test_unicode_dataset_and_gate_ids(self):
        payload = {
            "dataset": "订单明细",
            "records": [{"id": "r1", "age": 30}, {"id": "r2"}],
            "rules": [
                {"id": "年龄必填", "type": "required",
                 "options": {"field": "age"}}
            ],
            "gates": [
                {"rule_id": "门槛-1", "source_rule_id": "年龄必填",
                 "max_failed_ratio": 0.5, "severity": "info"}
            ],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["dataset"], "订单明细")
        self.assertEqual(body["results"][0]["rule_id"], "门槛-1")
        self.assertEqual(body["results"][0]["status"], "PASSED")


class CliQualityGatesErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn(code, body["error"]["code"])
        self.assertIn("message", body["error"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_payload_not_object(self):
        proc = run_json([1, 2])
        self.assert_error(proc, "INVALID_INPUT")

    def test_missing_dataset(self):
        payload = {k: v for k, v in PAYLOAD.items() if k != "dataset"}
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_INPUT")

    def test_missing_records(self):
        payload = {k: v for k, v in PAYLOAD.items() if k != "records"}
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_INPUT")

    def test_missing_rules(self):
        payload = {k: v for k, v in PAYLOAD.items() if k != "rules"}
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_INPUT")

    def test_missing_gates(self):
        payload = {k: v for k, v in PAYLOAD.items() if k != "gates"}
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_INPUT")

    def test_records_structure_invalid(self):
        payload = dict(PAYLOAD, records=[1, 2])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_INPUT")

    def test_rules_definition_invalid(self):
        payload = dict(PAYLOAD, rules=[{"id": "bad"}])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_RULE")

    def test_gate_not_object(self):
        payload = dict(PAYLOAD, gates=["g1"])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_gate_missing_key(self):
        bad_gate = dict(PAYLOAD["gates"][0])
        del bad_gate["severity"]
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_gate_extra_key(self):
        bad_gate = dict(PAYLOAD["gates"][0])
        bad_gate["extra"] = 1
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_duplicate_gate_rule_id(self):
        gate = dict(PAYLOAD["gates"][0])
        payload = dict(PAYLOAD, gates=[dict(gate), dict(gate)])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_invalid_severity(self):
        bad_gate = dict(PAYLOAD["gates"][0], severity="fatal")
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_ratio_bool_rejected(self):
        bad_gate = dict(PAYLOAD["gates"][0], max_failed_ratio=True)
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_ratio_out_of_range(self):
        bad_gate = dict(PAYLOAD["gates"][0], max_failed_ratio=1.5)
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_QUALITY_GATE_RULE")

    def test_unknown_source_rule_id(self):
        bad_gate = dict(PAYLOAD["gates"][0], source_rule_id="no-such-rule")
        payload = dict(PAYLOAD, gates=[bad_gate])
        proc = run_json(payload)
        self.assert_error(proc, "UNKNOWN_QUALITY_GATE_SOURCE")


if __name__ == "__main__":
    unittest.main()
