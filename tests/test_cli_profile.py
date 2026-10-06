"""End-to-end tests for the ``dq profile`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "profile"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class CliProfileSuccessTest(unittest.TestCase):
    def test_profile_payload(self):
        proc = run_json(
            {
                "records": [
                    {"id": "r1", "age": 3, "city": "北京"},
                    {"id": "r2", "age": 42, "city": "上海"},
                ],
                "fields": ["age", "city"],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["record_count"], 2)
        self.assertEqual([p["field"] for p in result["fields"]], ["age", "city"])
        age = result["fields"][0]
        self.assertEqual(age["numeric_min"], 3)
        self.assertEqual(age["numeric_max"], 42)
        ids = [rule["id"] for rule in result["rule_candidates"]]
        self.assertEqual(
            ids,
            [
                "profile:age:required",
                "profile:age:unique",
                "profile:age:range",
                "profile:age:allowed_values",
                "profile:city:required",
                "profile:city:unique",
                "profile:city:allowed_values",
            ],
        )
        # Non-ASCII is emitted literally, not escaped.
        self.assertIn("北京", proc.stdout.decode("utf-8"))

    def test_default_fields_empty_records(self):
        proc = run_json({"records": []})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["fields"], [])
        self.assertEqual(result["rule_candidates"], [])

    def test_explicit_fields_zero_profiles(self):
        proc = run_json({"records": [], "fields": ["a"]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["fields"][0]["field"], "a")
        self.assertEqual(result["fields"][0]["present_count"], 0)
        self.assertEqual(result["rule_candidates"], [])

    def test_fields_omitted_uses_first_appearance_order(self):
        proc = run_json(
            {"records": [{"b": 1, "a": 2}, {"c": 3, "b": 4}]}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            [p["field"] for p in result["fields"]], ["b", "a", "c"]
        )


class CliProfileErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(set(body["error"]), {"code", "message"})
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
        self._assert_error(proc, "INVALID_PROFILE_INPUT")

    def test_missing_records(self):
        proc = run_json({"fields": ["a"]})
        self._assert_error(proc, "INVALID_PROFILE_INPUT")

    def test_bad_records(self):
        proc = run_json({"records": [1]})
        self._assert_error(proc, "INVALID_PROFILE_INPUT")

    def test_records_not_list(self):
        proc = run_json({"records": {"a": 1}})
        self._assert_error(proc, "INVALID_PROFILE_INPUT")

    def test_bad_fields(self):
        proc = run_json({"records": [], "fields": ["a", "a"]})
        self._assert_error(proc, "INVALID_PROFILE_INPUT")
        proc = run_json({"records": [], "fields": "a"})
        self._assert_error(proc, "INVALID_PROFILE_INPUT")


if __name__ == "__main__":
    unittest.main()
