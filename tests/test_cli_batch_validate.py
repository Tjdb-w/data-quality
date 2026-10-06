"""End-to-end tests for the ``dq batch-validate`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "batch-validate"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


UNIQUE_KEY_RULE = {
    "id": "uk",
    "type": "unique_key",
    "options": {"fields": ["a", "b"]},
}

GROUP_RATIO_RULE = {
    "id": "gr",
    "type": "group_ratio",
    "options": {
        "group_fields": ["region"],
        "value_field": "status",
        "allowed_values": ["ok"],
        "min_ratio": 0.5,
    },
}


def payload(records, rules):
    return {"records": records, "rules": rules}


class CliBatchValidateSuccessTest(unittest.TestCase):
    def test_passing_batch_exits_zero(self):
        proc = run_json(
            payload(
                [{"id": "r1", "a": 1, "b": "x"}],
                [UNIQUE_KEY_RULE],
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(body["passed"])
        self.assertEqual(body["violations"], [])
        self.assertEqual(
            body["summary"],
            {
                "record_count": 1,
                "rule_count": 1,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )

    def test_violations_still_exit_zero(self):
        proc = run_json(
            payload(
                [
                    {"id": "r1", "a": 1, "b": "x"},
                    {"id": "r2", "a": 1, "b": "x"},
                    {"id": "r3", "region": "cn", "status": "bad"},
                ],
                [UNIQUE_KEY_RULE, GROUP_RATIO_RULE],
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(body["passed"])
        self.assertEqual(body["summary"]["violation_count"], 3)
        self.assertEqual(body["summary"]["checked_group_count"], 2)
        first = body["violations"][0]
        second = body["violations"][2]
        self.assertEqual(first["rule_id"], "uk")
        self.assertEqual(first["record_index"], 1)
        self.assertEqual(first["record_id"], "r2")
        self.assertEqual(first["message"], "is a duplicate unique key")
        self.assertEqual(second["rule_id"], "gr")
        self.assertIsNone(second["record_index"])
        self.assertIsNone(second["record_id"])
        self.assertEqual(second["group"], {"region": "cn"})
        self.assertEqual(second["record_count"], 1)
        self.assertEqual(second["matched_count"], 0)
        self.assertEqual(second["ratio"], 0)
        self.assertEqual(
            second["samples"],
            [
                {
                    "record_index": 2,
                    "record_id": "r3",
                    "value": "bad",
                    "matched": False,
                }
            ],
        )

    def test_empty_records_and_rules_pass(self):
        proc = run_json(payload([], []))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(body["passed"])
        self.assertEqual(
            body["summary"],
            {
                "record_count": 0,
                "rule_count": 0,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )


class CliBatchValidateErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(sorted(body), ["error"])
        self.assertEqual(sorted(body["error"]), ["code", "message"])
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_invalid_utf8(self):
        proc = run_cli(b"\xff\xfe")
        self.assert_error(proc, "INVALID_JSON")

    def test_payload_must_be_an_object(self):
        self.assert_error(run_cli(b"[1, 2]"), "INVALID_BATCH_INPUT")

    def test_payload_requires_records_and_rules(self):
        self.assert_error(run_json({"rules": []}), "INVALID_BATCH_INPUT")
        self.assert_error(run_json({"records": []}), "INVALID_BATCH_INPUT")

    def test_malformed_records(self):
        self.assert_error(
            run_json(payload("nope", [])), "INVALID_BATCH_INPUT"
        )
        self.assert_error(
            run_json(payload([{"a": 1}, 2], [])), "INVALID_BATCH_INPUT"
        )

    def test_malformed_rules(self):
        self.assert_error(
            run_json(payload([], "nope")), "INVALID_BATCH_RULE"
        )
        self.assert_error(
            run_json(
                payload(
                    [],
                    [
                        {
                            "id": "uk",
                            "type": "unique_key",
                            "options": {"fields": []},
                        }
                    ],
                )
            ),
            "INVALID_BATCH_RULE",
        )

    def test_rule_errors_win_over_record_errors(self):
        self.assert_error(
            run_json(payload("bad-records", "bad-rules")),
            "INVALID_BATCH_RULE",
        )


if __name__ == "__main__":
    unittest.main()
