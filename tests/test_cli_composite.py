"""End-to-end tests for composite rules via the command line interface."""

import json
import subprocess
import sys
import unittest


VALIDATE = [sys.executable, "-m", "data_quality", "validate"]
QUERY = [sys.executable, "-m", "data_quality", "query-results"]


def run(cmd, obj):
    return subprocess.run(
        cmd,
        input=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )


COMPOSITE_RULE = {
    "rule_id": "dates",
    "fields": ["start", "end"],
    "severity": "error",
    "conditions": [
        {"type": "date_before", "earlier_field": "start", "later_field": "end"}
    ],
}


class CliCompositeValidateTest(unittest.TestCase):
    def test_composite_results_appended_without_changing_single_field(self):
        proc = run(VALIDATE, {
            "records": [
                {"id": "r1", "start": "2026-01-01", "end": "2026-02-01"},
                {"id": "r2", "start": "2026-03-01", "end": "2026-02-01"},
            ],
            "rules": [
                {"id": "req", "type": "required", "options": {"field": "start"}}
            ],
            "dataset": "ds",
            "composite_rules": [COMPOSITE_RULE],
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["summary"]["checked_rule_count"], 1)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["composite_rule_count"], 1)
        statuses = [r["status"] for r in result["composite_results"]]
        self.assertEqual(statuses, ["PASSED", "FAILED"])
        failed = result["composite_results"][1]
        self.assertEqual(failed["dataset_id"], "ds")
        self.assertEqual(failed["record_id"], "r2")
        self.assertEqual(failed["fields"], ["start", "end"])

    def test_skipped_missing_field(self):
        proc = run(VALIDATE, {
            "records": [{"id": "r1"}],
            "rules": [],
            "dataset": "ds",
            "composite_rules": [COMPOSITE_RULE],
        })
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            result["composite_results"][0]["status"], "SKIPPED_MISSING_FIELD"
        )

    def test_legacy_payload_still_works(self):
        proc = run(VALIDATE, {
            "records": [{"id": "r1", "start": "2026-01-01"}],
            "rules": [
                {"id": "req", "type": "required", "options": {"field": "start"}}
            ],
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("composite_results", result)

    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def _rules_payload(self, **overrides):
        payload = {
            "records": [],
            "rules": [],
            "dataset": "ds",
            "composite_rules": [COMPOSITE_RULE],
        }
        payload.update(overrides)
        return payload

    def test_empty_rule_set_error(self):
        proc = run(VALIDATE, self._rules_payload(composite_rules=[]))
        self._assert_error(proc, "INVALID_RULE_SET")

    def test_missing_dataset_error(self):
        payload = self._rules_payload()
        del payload["dataset"]
        proc = run(VALIDATE, payload)
        self._assert_error(proc, "INVALID_RULE_SET")

    def test_duplicate_rule_id_error(self):
        proc = run(
            VALIDATE,
            self._rules_payload(
                composite_rules=[COMPOSITE_RULE, COMPOSITE_RULE]
            ),
        )
        self._assert_error(proc, "DUPLICATE_RULE_ID")

    def test_invalid_severity_error(self):
        bad = dict(COMPOSITE_RULE, severity="fatal")
        proc = run(VALIDATE, self._rules_payload(composite_rules=[bad]))
        self._assert_error(proc, "INVALID_SEVERITY")

    def test_unsupported_condition_error(self):
        bad = {
            "rule_id": "x",
            "fields": ["a", "b"],
            "severity": "info",
            "conditions": [
                {"type": "magic", "left_field": "a", "right_field": "b"}
            ],
        }
        proc = run(VALIDATE, self._rules_payload(composite_rules=[bad]))
        self._assert_error(proc, "UNSUPPORTED_COMPOSITE_CONDITION")

    def test_invalid_composite_rule_errors(self):
        cases = [
            # field not declared in the rule's field set
            {"rule_id": "x", "fields": ["a"], "severity": "info",
             "conditions": [
                 {"type": "field_equal", "left_field": "a", "right_field": "b"}]},
            # condition references itself
            {"rule_id": "x", "fields": ["a"], "severity": "info",
             "conditions": [
                 {"type": "field_equal", "left_field": "a", "right_field": "a"}]},
            # illegal date format
            dict(COMPOSITE_RULE,
                 conditions=[
                     {"type": "date_before", "earlier_field": "start",
                      "later_field": "end", "format": "DD/MM/YYYY"}]),
        ]
        for bad in cases:
            proc = run(VALIDATE, self._rules_payload(composite_rules=[bad]))
            self._assert_error(proc, "INVALID_COMPOSITE_RULE")

    def test_invalid_record_reference_error(self):
        proc = run(VALIDATE, self._rules_payload(
            records=[{"id": "r1", "start": "2026-01-01", "end": "2026-02-01"}],
            record_refs=[{"record_index": 9}],
        ))
        self._assert_error(proc, "INVALID_RECORD_REFERENCE")


class CliQueryResultsTest(unittest.TestCase):
    RESULTS = [
        {"dataset_id": "ds", "record_index": 0, "record_id": "r1",
         "rule_id": "c1", "fields": ["a", "b"], "status": "FAILED",
         "severity": "error", "context": {}},
        {"dataset_id": "ds", "record_index": 0, "record_id": "r1",
         "rule_id": "c2", "fields": ["a"], "status": "PASSED",
         "severity": "warning", "context": {}},
        {"dataset_id": "ds", "record_index": 1, "record_id": "r2",
         "rule_id": "c1", "fields": ["a", "b"], "status": "PASSED",
         "severity": "error", "context": {}},
    ]

    def test_no_filters_returns_all_in_order(self):
        proc = run(QUERY, {"results": self.RESULTS})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["count"], 3)
        self.assertEqual(
            [(r["record_index"], r["rule_id"]) for r in body["results"]],
            [(0, "c1"), (0, "c2"), (1, "c1")],
        )

    def test_combined_filters(self):
        proc = run(QUERY, {
            "results": self.RESULTS,
            "rule_id": "c1",
            "severity": "error",
            "record_id": "r1",
        })
        self.assertEqual(proc.returncode, 0)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["record_index"], 0)

    def test_filter_by_record_index(self):
        proc = run(QUERY, {"results": self.RESULTS, "record_index": 1})
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual([r["rule_id"] for r in body["results"]], ["c1"])

    def test_missing_results_key(self):
        proc = run(QUERY, {})
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
