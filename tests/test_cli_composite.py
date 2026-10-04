"""End-to-end tests for the composite rule CLI subcommands."""

import json
import subprocess
import sys
import unittest


PKG_ROOT = [sys.executable, "-m", "data_quality"]

DATASET = {
    "dataset_id": "ds",
    "fields": {
        "start": "date",
        "end": "date",
        "kind": "string",
        "closed_at": "datetime",
    },
}
RULES = [
    {
        "rule_id": "dates",
        "fields": ["start", "end"],
        "severity": "error",
        "conditions": [
            {"type": "date_order", "left": "start", "right": "end"}
        ],
    },
    {
        "rule_id": "needs-close",
        "fields": ["kind", "closed_at"],
        "severity": "warning",
        "conditions": [
            {"type": "required_if", "when": "kind", "field": "closed_at"}
        ],
    },
]
RECORDS = [
    {"id": "s1", "start": "2024-01-01", "end": "2024-02-01",
     "kind": None, "closed_at": None},
    {"id": "s2", "start": "2024-05-01", "end": "2024-01-01",
     "kind": "done", "closed_at": None},
    {"id": "s3", "start": "2024-01-01"},
]


def run(command, obj):
    proc = subprocess.run(
        PKG_ROOT + [command],
        input=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )
    return proc


class CompositeRegisterCliTest(unittest.TestCase):
    def test_success(self):
        proc = run("composite-register", {"dataset": DATASET, "rules": RULES})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            [rule["rule_id"] for rule in body["compiled_rules"]],
            ["dates", "needs-close"],
        )

    def test_error_codes(self):
        cases = [
            ({"dataset": DATASET, "rules": []}, "INVALID_RULE_SET"),
            ({"dataset": DATASET, "rules": [RULES[0], dict(RULES[0])]},
             "DUPLICATE_RULE_ID"),
            ({"dataset": DATASET,
              "rules": [dict(RULES[0], severity="fatal")]},
             "INVALID_SEVERITY"),
            ({"dataset": DATASET,
              "rules": [dict(
                  RULES[0],
                  conditions=[{"type": "nope", "left": "start",
                               "right": "end"}])]},
             "UNSUPPORTED_COMPOSITE_CONDITION"),
            ({"dataset": DATASET,
              "rules": [dict(
                  RULES[0],
                  fields=["start", "ghost"],
                  conditions=[{"type": "field_equals", "left": "start",
                               "right": "ghost"}])]},
             "INVALID_COMPOSITE_RULE"),
        ]
        for payload, code in cases:
            proc = run("composite-register", payload)
            self.assertEqual(proc.returncode, 2, payload)
            body = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(body["error"]["code"], code)
            self.assertTrue(body["error"]["message"])


class CompositeEvaluateCliTest(unittest.TestCase):
    def test_register_then_evaluate_round_trip(self):
        reg = run("composite-register", {"dataset": DATASET, "rules": RULES})
        compiled = json.loads(reg.stdout.decode("utf-8"))["compiled_rules"]
        proc = run("composite-evaluate",
                   {"records": RECORDS, "compiled_rules": compiled})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["dataset_id"], "ds")
        self.assertEqual(
            [(r["rule_id"], r["record_id"], r["conclusion"])
             for r in body["results"]],
            [
                ("dates", "s1", "PASSED"),
                ("dates", "s2", "FAILED"),
                ("dates", "s3", "SKIPPED_MISSING_FIELD"),
                ("needs-close", "s1", "PASSED"),
                ("needs-close", "s2", "FAILED"),
                ("needs-close", "s3", "SKIPPED_MISSING_FIELD"),
            ],
        )
        self.assertEqual(body["summary"]["failed_count"], 2)
        self.assertEqual(body["summary"]["skipped_count"], 2)

    def test_register_and_evaluate_in_one_payload(self):
        proc = run("composite-evaluate",
                   {"records": RECORDS, "dataset": DATASET, "rules": RULES})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(len(body["results"]), 6)

    def test_invalid_record_reference(self):
        reg = run("composite-register", {"dataset": DATASET, "rules": RULES})
        compiled = json.loads(reg.stdout.decode("utf-8"))["compiled_rules"]
        proc = run("composite-evaluate",
                   {"records": ["not-an-object"], "compiled_rules": compiled})
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_RECORD_REFERENCE")

        proc = run("composite-evaluate", {"records": RECORDS})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8"))["error"]["code"],
            "INVALID_RECORD_REFERENCE",
        )


class CompositeQueryCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reg = run("composite-register", {"dataset": DATASET, "rules": RULES})
        compiled = json.loads(reg.stdout.decode("utf-8"))["compiled_rules"]
        evaluation = run(
            "composite-evaluate",
            {"records": RECORDS, "compiled_rules": compiled},
        )
        cls.output = json.loads(evaluation.stdout.decode("utf-8"))

    def test_filter_by_rule_severity_and_record(self):
        proc = run(
            "composite-query",
            {"results": self.output,
             "filters": {"rule_id": "needs-close", "severity": "warning",
                         "record_id": "s2"}},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        selected = json.loads(proc.stdout.decode("utf-8"))["results"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["conclusion"], "FAILED")

    def test_filters_are_optional_and_order_is_stable(self):
        proc = run("composite-query", {"results": self.output})
        selected = json.loads(proc.stdout.decode("utf-8"))["results"]
        self.assertEqual(
            [(r["rule_id"], r["record_index"]) for r in selected],
            [(r["rule_id"], r["record_index"]) for r in self.output["results"]],
        )

    def test_bad_filter(self):
        proc = run(
            "composite-query",
            {"results": self.output, "filters": {"severity": "unknown"}},
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8"))["error"]["code"],
            "INVALID_RECORD_REFERENCE",
        )


class ValidateWithCompositeCliTest(unittest.TestCase):
    def test_one_run_returns_both_result_kinds(self):
        payload = {
            "records": [
                {"id": "s1", "name": "n", "start": "2024-01-01",
                 "end": "2024-02-01"},
                {"id": "s2", "start": "2024-05-01", "end": "2024-01-01"},
            ],
            "rules": [
                {"id": "name-req", "type": "required",
                 "options": {"field": "name"}}
            ],
            "dataset": {
                "dataset_id": "ds",
                "fields": ["name", "start", "end"],
            },
            "composite_rules": [RULES[0]],
        }
        proc = run("validate", payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        # Baseline single-field output is unchanged.
        self.assertEqual(
            [v["rule_id"] for v in body["violations"]], ["name-req"]
        )
        self.assertFalse(body["passed"])
        self.assertEqual(body["summary"]["checked_rule_count"], 1)
        # Composite results are attached alongside.
        self.assertEqual(
            [(r["record_id"], r["conclusion"])
             for r in body["composite"]["results"]],
            [("s1", "PASSED"), ("s2", "FAILED")],
        )

    def test_composite_registration_failure_aborts_run(self):
        payload = {
            "records": "not-a-list",
            "rules": [],
            "dataset": DATASET,
            "composite_rules": [RULES[0], dict(RULES[0])],
        }
        proc = run("validate", payload)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8"))["error"]["code"],
            "DUPLICATE_RULE_ID",
        )

    def test_dataset_and_composite_rules_go_together(self):
        payload = {
            "records": [],
            "rules": [],
            "composite_rules": RULES,
        }
        proc = run("validate", payload)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8"))["error"]["code"],
            "INVALID_COMPOSITE_RULE",
        )

    def test_baseline_payload_without_composite(self):
        proc = run(
            "validate",
            {"records": [{"id": 1}],
             "rules": [{"id": "r", "type": "required",
                        "options": {"field": "x"}}]},
        )
        self.assertEqual(proc.returncode, 0)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("composite", body)


if __name__ == "__main__":
    unittest.main()
