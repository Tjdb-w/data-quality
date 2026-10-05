"""End-to-end tests for the ``dq reference-integrity`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "reference-integrity"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


PAYLOAD = {
    "datasets": {
        "customers": [{"id": "c1"}, {"id": "c2"}],
        "orders": [
            {"id": "o1", "customer_id": "c1"},
            {"id": "o2", "customer_id": "c9"},
            {"id": "o3", "customer_id": None},
        ],
    },
    "rules": [
        {
            "rule_id": "订单-客户",
            "source_dataset": "orders",
            "source_field": "customer_id",
            "target_dataset": "customers",
            "target_field": "id",
        }
    ],
}


class CliReferenceIntegritySuccessTest(unittest.TestCase):
    def test_violation_output(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "passed": False,
                "summary": {
                    "dataset_count": 2,
                    "rule_count": 1,
                    "checked_value_count": 2,
                    "violation_count": 1,
                },
                "violations": [
                    {
                        "rule_id": "订单-客户",
                        "source": {
                            "dataset": "orders",
                            "record_index": 1,
                            "record_id": "o2",
                            "field": "customer_id",
                            "value": "c9",
                        },
                        "target": {"dataset": "customers", "field": "id"},
                        "message": "has no matching target value",
                    }
                ],
            },
        )

    def test_output_is_a_single_line(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)

    def test_passed_output(self):
        payload = {
            "datasets": {
                "customers": [{"id": "c1"}],
                "orders": [{"customer_id": "c1"}],
            },
            "rules": PAYLOAD["rules"],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(body["passed"])
        self.assertEqual(body["violations"], [])

    def test_composite_rule_output(self):
        payload = {
            "datasets": {
                "shipment_lines": [
                    {"order_id": "o1", "line_no": 1},
                    {"order_id": "o2", "line_no": None},
                ],
                "order_items": [
                    {"id": "i1", "order_id": "o1", "line_no": 1},
                    {"id": "i2", "order_id": "o9", "line_no": 9},
                    {"id": "i3", "order_id": "o1"},
                ],
            },
            "rules": [
                {
                    "rule_id": "明细-发货",
                    "source_dataset": "order_items",
                    "source_fields": ["order_id", "line_no"],
                    "target_dataset": "shipment_lines",
                    "target_fields": ["order_id", "line_no"],
                }
            ],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "passed": False,
                "summary": {
                    "dataset_count": 2,
                    "rule_count": 1,
                    "checked_value_count": 3,
                    "violation_count": 2,
                },
                "violations": [
                    {
                        "rule_id": "明细-发货",
                        "source": {
                            "dataset": "order_items",
                            "record_index": 1,
                            "record_id": "i2",
                            "field": ["order_id", "line_no"],
                            "value": ["o9", 9],
                        },
                        "target": {
                            "dataset": "shipment_lines",
                            "field": ["order_id", "line_no"],
                        },
                        "message": "has no matching target value",
                    },
                    {
                        "rule_id": "明细-发货",
                        "source": {
                            "dataset": "order_items",
                            "record_index": 2,
                            "record_id": "i3",
                            "field": ["order_id", "line_no"],
                            "value": ["o1", None],
                        },
                        "target": {
                            "dataset": "shipment_lines",
                            "field": ["order_id", "line_no"],
                        },
                        "message": "has an incomplete composite reference",
                    },
                ],
            },
        )


class CliReferenceIntegrityErrorTest(unittest.TestCase):
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
        self.assert_error(proc, "INVALID_REFERENCE_INPUT")

    def test_missing_datasets(self):
        proc = run_json({"rules": []})
        self.assert_error(proc, "INVALID_REFERENCE_INPUT")

    def test_record_not_object(self):
        proc = run_json({"datasets": {"a": [1]}, "rules": []})
        self.assert_error(proc, "INVALID_REFERENCE_INPUT")

    def test_rule_missing_key(self):
        payload = {
            "datasets": {},
            "rules": [{"rule_id": "r1"}],
        }
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_REFERENCE_RULE")

    def test_duplicate_rule_id(self):
        rule = dict(PAYLOAD["rules"][0])
        proc = run_json(
            {"datasets": PAYLOAD["datasets"], "rules": [rule, dict(rule)]}
        )
        self.assert_error(proc, "INVALID_REFERENCE_RULE")

    def test_unknown_dataset(self):
        proc = run_json({"datasets": {}, "rules": PAYLOAD["rules"]})
        self.assert_error(proc, "UNKNOWN_REFERENCE_DATASET")

    def test_composite_rule_length_mismatch(self):
        payload = {
            "datasets": {},
            "rules": [
                {
                    "rule_id": "r1",
                    "source_dataset": "a",
                    "source_fields": ["x", "y"],
                    "target_dataset": "b",
                    "target_fields": ["x"],
                }
            ],
        }
        proc = run_json(payload)
        self.assert_error(proc, "INVALID_REFERENCE_RULE")

    def test_mixed_rule_shape(self):
        rule = dict(PAYLOAD["rules"][0])
        rule["source_fields"] = ["customer_id"]
        proc = run_json({"datasets": PAYLOAD["datasets"], "rules": [rule]})
        self.assert_error(proc, "INVALID_REFERENCE_RULE")


if __name__ == "__main__":
    unittest.main()
