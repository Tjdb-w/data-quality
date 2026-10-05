"""End-to-end tests for the ``dq reference-integrity`` command."""

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


def ref_rule(
    rule_id="fk",
    source_dataset="orders",
    source_field="customer_id",
    target_dataset="customers",
    target_field="id",
):
    return {
        "rule_id": rule_id,
        "source_dataset": source_dataset,
        "source_field": source_field,
        "target_dataset": target_dataset,
        "target_field": target_field,
    }


class CliSuccessTest(unittest.TestCase):
    def test_violation_payload(self):
        proc = run_json(
            {
                "datasets": {
                    "orders": [
                        {"id": "o1", "customer_id": "c1"},
                        {"id": "o2", "customer_id": "cX"},
                        {"id": "o3"},
                        {"id": "o4", "customer_id": None},
                    ],
                    "customers": [{"id": "c1"}],
                },
                "rules": [ref_rule()],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["summary"],
            {
                "dataset_count": 2,
                "rule_count": 1,
                "checked_value_count": 2,
                "violation_count": 1,
            },
        )
        self.assertEqual(result["violations"][0]["source"]["record_id"], "o2")
        self.assertEqual(set(result), {"passed", "summary", "violations"})

    def test_passing_payload(self):
        proc = run_json(
            {
                "datasets": {
                    "orders": [{"id": "o1", "customer_id": "c1"}],
                    "customers": [{"id": "c1"}],
                },
                "rules": [ref_rule()],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["checked_value_count"], 1)

    def test_utf8_round_trip(self):
        proc = run_json(
            {
                "datasets": {
                    "orders": [{"id": "o1", "customer_id": "广州"}],
                    "customers": [{"id": "北京"}],
                },
                "rules": [ref_rule()],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["violations"][0]["source"]["value"], "广州"
        )
        # Non-ASCII is emitted literally, not escaped.
        self.assertIn("广州", proc.stdout.decode("utf-8"))


class CliErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")

    def test_invalid_utf8(self):
        self._assert_error(run_cli(b'{"datasets": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_REFERENCE_INPUT")

    def test_missing_datasets(self):
        self._assert_error(run_json({"rules": []}), "INVALID_REFERENCE_INPUT")

    def test_missing_rules(self):
        self._assert_error(
            run_json({"datasets": {}}), "INVALID_REFERENCE_INPUT"
        )

    def test_record_not_object(self):
        self._assert_error(
            run_json({"datasets": {"a": [1]}, "rules": []}),
            "INVALID_REFERENCE_INPUT",
        )

    def test_bad_rule_keys(self):
        self._assert_error(
            run_json(
                {
                    "datasets": {"a": [], "b": []},
                    "rules": [
                        {
                            "rule_id": "r",
                            "source_dataset": "a",
                            "source_field": "f",
                            "target_dataset": "b",
                        }
                    ],
                }
            ),
            "INVALID_REFERENCE_RULE",
        )

    def test_duplicate_rule_id(self):
        self._assert_error(
            run_json(
                {
                    "datasets": {"a": [], "b": []},
                    "rules": [ref_rule("dup"), ref_rule("dup")],
                }
            ),
            "INVALID_REFERENCE_RULE",
        )

    def test_unknown_dataset(self):
        self._assert_error(
            run_json(
                {"datasets": {"a": []}, "rules": [ref_rule(target_dataset="b")]}
            ),
            "UNKNOWN_REFERENCE_DATASET",
        )


if __name__ == "__main__":
    unittest.main()
