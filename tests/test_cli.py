"""End-to-end tests for the ``dq validate`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "validate"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    )


class CliSuccessTest(unittest.TestCase):
    def test_pass_and_fail_payloads(self):
        proc = run_json(
            {
                "records": [
                    {"id": "r1", "name": "甲"},
                    {"id": "r2", "name": "乙"},
                ],
                "rules": [
                    {"id": "req", "type": "required", "options": {"field": "name"}}
                ],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["summary"]["record_count"], 2)

        proc = run_json(
            {
                "records": [{"id": "r1"}, {"id": "r2", "name": "乙"}],
                "rules": [
                    {"id": "req", "type": "required", "options": {"field": "name"}}
                ],
            }
        )
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["violations"][0]["record_index"], 0)

    def test_utf8_round_trip(self):
        proc = run_json(
            {
                "records": [
                    {"id": "中文-id", "city": "北京"},
                    {"id": "x", "city": "广州"},
                ],
                "rules": [
                    {
                        "id": "cities",
                        "type": "allowed_values",
                        "options": {"field": "city", "values": ["北京", "上海"]},
                    }
                ],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["violations"][0]["record_id"], "x")
        self.assertEqual(result["violations"][0]["value"], "广州")
        # Non-ASCII is emitted literally, not escaped.
        self.assertIn("广州", proc.stdout.decode("utf-8"))


class CliErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self._assert_error(proc, "INVALID_JSON")

    def test_invalid_utf8(self):
        proc = run_cli(b'{"records": [\xff]}')
        self._assert_error(proc, "INVALID_JSON")

    def test_payload_not_object(self):
        proc = run_json([1, 2])
        self._assert_error(proc, "INVALID_INPUT")

    def test_missing_keys(self):
        proc = run_json({"records": []})
        self._assert_error(proc, "INVALID_INPUT")
        proc = run_json({"rules": []})
        self._assert_error(proc, "INVALID_INPUT")

    def test_bad_records(self):
        proc = run_json({"records": [1], "rules": []})
        self._assert_error(proc, "INVALID_INPUT")

    def test_bad_rule(self):
        proc = run_json(
            {
                "records": [],
                "rules": [{"id": "r", "type": "mystery", "options": {}}],
            }
        )
        self._assert_error(proc, "INVALID_RULE")

    def test_duplicate_rule_id(self):
        proc = run_json(
            {
                "records": [],
                "rules": [
                    {"id": "r", "type": "required", "options": {"field": "a"}},
                    {"id": "r", "type": "required", "options": {"field": "b"}},
                ],
            }
        )
        self._assert_error(proc, "INVALID_RULE")

    def test_range_conflict(self):
        proc = run_json(
            {
                "records": [],
                "rules": [
                    {
                        "id": "r",
                        "type": "range",
                        "options": {"field": "a", "min": 9, "max": 1},
                    }
                ],
            }
        )
        self._assert_error(proc, "INVALID_RULE")

    def test_bad_regex(self):
        proc = run_json(
            {
                "records": [],
                "rules": [
                    {
                        "id": "r",
                        "type": "regex",
                        "options": {"field": "a", "pattern": "("},
                    }
                ],
            }
        )
        self._assert_error(proc, "INVALID_RULE")


if __name__ == "__main__":
    unittest.main()
