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
    return run_cli(
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    )


class CliProfileSuccessTest(unittest.TestCase):
    def test_profile_payload(self):
        proc = run_json(
            {
                "records": [
                    {"id": "r1", "age": 1, "city": "北京"},
                    {"id": "r2", "age": 42, "city": None},
                ],
                "fields": ["age", "city"],
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(
            [f["field"] for f in result["fields"]], ["age", "city"]
        )
        age = result["fields"][0]
        self.assertEqual(age["present_count"], 2)
        self.assertEqual(age["numeric_min"], 1)
        self.assertEqual(age["numeric_max"], 42)
        city = result["fields"][1]
        self.assertEqual(city["null_count"], 1)
        self.assertEqual(city["non_null_count"], 1)
        self.assertEqual(
            city["type_counts"],
            {"null": 1, "boolean": 0, "number": 0,
             "string": 1, "array": 0, "object": 0},
        )
        ids = [c["id"] for c in result["rule_candidates"]]
        self.assertEqual(
            ids,
            [
                "profile:age:required",
                "profile:age:unique",
                "profile:age:range",
                "profile:age:allowed_values",
                "profile:city:required",
                "profile:city:allowed_values",
            ],
        )
        # Non-ASCII is emitted literally, not escaped.
        self.assertIn("北京", proc.stdout.decode("utf-8"))

    def test_fields_optional_and_empty_records(self):
        proc = run_json({"records": []})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result, {"record_count": 0, "fields": [],
                                  "rule_candidates": []})

    def test_explicit_fields_on_empty_records(self):
        proc = run_json({"records": [], "fields": ["a"]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(len(result["fields"]), 1)
        self.assertEqual(result["fields"][0]["field"], "a")
        self.assertEqual(result["fields"][0]["present_count"], 0)
        self.assertEqual(result["rule_candidates"], [])

    def test_rule_candidates_round_trip_into_validate(self):
        records = [{"id": "r1", "n": 1}, {"id": "r2", "n": 2}]
        proc = run_json({"records": records})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        profile = json.loads(proc.stdout.decode("utf-8"))

        validate_proc = subprocess.run(
            [sys.executable, "-m", "data_quality", "validate"],
            input=json.dumps(
                {"records": records, "rules": profile["rule_candidates"]}
            ).encode("utf-8"),
            capture_output=True,
        )
        self.assertEqual(validate_proc.returncode, 0, validate_proc.stderr)
        validated = json.loads(validate_proc.stdout.decode("utf-8"))
        self.assertTrue(validated["passed"])


class CliProfileErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, {"error": {"code": code,
                                          "message": body["error"]["message"]}})
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{not json"), "INVALID_JSON")

    def test_invalid_utf8(self):
        self._assert_error(run_cli(b'{"records": [\xff]}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_PROFILE_INPUT")

    def test_missing_records(self):
        self._assert_error(run_json({"fields": ["a"]}),
                           "INVALID_PROFILE_INPUT")

    def test_records_not_list(self):
        self._assert_error(run_json({"records": {}}),
                           "INVALID_PROFILE_INPUT")

    def test_record_not_object(self):
        self._assert_error(run_json({"records": [1]}),
                           "INVALID_PROFILE_INPUT")

    def test_bad_fields(self):
        self._assert_error(
            run_json({"records": [], "fields": ["a", "a"]}),
            "INVALID_PROFILE_INPUT",
        )
        self._assert_error(
            run_json({"records": [], "fields": [""]}),
            "INVALID_PROFILE_INPUT",
        )

    def test_no_partial_output_on_error(self):
        proc = run_json({"records": [{"a": 1}], "fields": [1]})
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("fields", body)
        self.assertNotIn("rule_candidates", body)


if __name__ == "__main__":
    unittest.main()
