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


UNIQUE_RULE = {
    "id": "uk",
    "type": "unique_key",
    "options": {"fields": ["country", "city"]},
}

RATIO_RULE = {
    "id": "gr",
    "type": "group_ratio",
    "options": {
        "group_fields": ["country", "city"],
        "value_field": "status",
        "allowed_values": ["ok"],
        "min_ratio": 0.5,
    },
}


def payload(records, rules):
    return {"records": records, "rules": rules}


class CliBatchValidateSuccessTest(unittest.TestCase):
    def test_full_report_shape(self):
        records = [
            {"id": "r1", "country": "US", "city": "NYC", "status": "ok"},
            {"id": "r2", "country": "US", "city": "NYC", "status": "bad"},
            {"id": "r3", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r4", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r5", "country": "FR", "city": None, "status": "ok"},
        ]
        proc = run_json(payload(records, [UNIQUE_RULE, RATIO_RULE]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body["summary"],
            {
                "record_count": 5,
                "rule_count": 2,
                "checked_group_count": 2,
                "violation_count": 4,
            },
        )
        self.assertFalse(body["passed"])
        unique_events = [
            (v["rule_id"], v["record_index"], v["message"])
            for v in body["violations"]
            if v["rule_id"] == "uk"
        ]
        self.assertEqual(
            unique_events,
            [
                ("uk", 1, "is a duplicate unique key"),
                ("uk", 3, "is a duplicate unique key"),
                ("uk", 4, "has an incomplete unique key"),
            ],
        )
        group_violation = next(
            v for v in body["violations"] if v["rule_id"] == "gr"
        )
        self.assertEqual(group_violation["record_index"], 2)
        self.assertNotIn("message", group_violation)
        self.assertEqual(group_violation["record_count"], 2)
        self.assertEqual(group_violation["matched_count"], 0)
        self.assertEqual(group_violation["ratio"], 0.0)

    def test_unique_key_violation_shape(self):
        records = [
            {"id": "r1", "country": "US", "city": "NYC"},
            {"id": "r2", "country": "US", "city": "NYC"},
        ]
        proc = run_json(payload(records, [UNIQUE_RULE]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body["violations"],
            [
                {
                    "rule_id": "uk",
                    "record_index": 1,
                    "record_id": "r2",
                    "fields": ["country", "city"],
                    "value": ["US", "NYC"],
                    "message": "is a duplicate unique key",
                }
            ],
        )

    def test_group_ratio_violation_shape(self):
        records = [
            {"id": "r1", "country": "US", "city": "LA", "status": "bad"},
            {"id": "r2", "country": "US", "city": "LA", "status": "ok"},
            {"id": "r3", "country": "US", "city": "LA", "status": "bad"},
        ]
        proc = run_json(payload(records, [RATIO_RULE]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body["violations"],
            [
                {
                    "rule_id": "gr",
                    "record_index": 0,
                    "record_id": "r1",
                    "group": [
                        {"field": "country", "value": "US"},
                        {"field": "city", "value": "LA"},
                    ],
                    "record_count": 3,
                    "matched_count": 1,
                    "ratio": 1 / 3,
                    "samples": [
                        {
                            "record_index": 0,
                            "record_id": "r1",
                            "value": "bad",
                            "matched": False,
                        },
                        {
                            "record_index": 1,
                            "record_id": "r2",
                            "value": "ok",
                            "matched": True,
                        },
                        {
                            "record_index": 2,
                            "record_id": "r3",
                            "value": "bad",
                            "matched": False,
                        },
                    ],
                }
            ],
        )

    def test_ratio_equal_threshold_passes_and_exits_zero(self):
        records = [
            {"id": "r1", "g": "x", "v": "ok"},
            {"id": "r2", "g": "x", "v": "bad"},
        ]
        rule = {
            "id": "gr",
            "type": "group_ratio",
            "options": {
                "group_fields": ["g"],
                "value_field": "v",
                "allowed_values": ["ok"],
                "min_ratio": 0.5,
            },
        }
        proc = run_json(payload(records, [rule]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(body["passed"])

    def test_empty_records_and_rules(self):
        proc = run_json(payload([], []))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(body["passed"])
        self.assertEqual(body["violations"], [])
        self.assertEqual(
            body["summary"],
            {
                "record_count": 0,
                "rule_count": 0,
                "checked_group_count": 0,
                "violation_count": 0,
            },
        )

    def test_output_is_a_single_line(self):
        proc = run_json(
            payload(
                [{"id": "r1", "g": "x", "v": "bad"}],
                [
                    {
                        "id": "gr",
                        "type": "group_ratio",
                        "options": {
                            "group_fields": ["g"],
                            "value_field": "v",
                            "allowed_values": ["ok"],
                            "min_ratio": 1,
                        },
                    }
                ],
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)


class CliBatchValidateErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            set(body), {"error"}, body
        )
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("message", body["error"])
        self.assertEqual(set(body["error"]), {"code", "message"})

    def test_invalid_json(self):
        self.assert_error(run_cli(b"{not json"), "INVALID_JSON")

    def test_payload_not_object(self):
        self.assert_error(run_json([1, 2]), "INVALID_BATCH_INPUT")

    def test_missing_records(self):
        self.assert_error(run_json({"rules": []}), "INVALID_BATCH_INPUT")

    def test_missing_rules(self):
        self.assert_error(run_json({"records": []}), "INVALID_BATCH_INPUT")

    def test_extra_top_level_key(self):
        self.assert_error(
            run_json({"records": [], "rules": [], "dataset": "x"}),
            "INVALID_BATCH_INPUT",
        )

    def test_records_malformed(self):
        self.assert_error(
            run_json(payload([1], [])), "INVALID_BATCH_INPUT"
        )

    def test_rules_not_list(self):
        self.assert_error(
            run_json(payload([], {"id": "u"})), "INVALID_BATCH_RULE"
        )

    def test_rule_missing_key(self):
        bad = dict(UNIQUE_RULE)
        del bad["type"]
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_BATCH_RULE"
        )

    def test_rule_extra_key(self):
        bad = dict(UNIQUE_RULE)
        bad["severity"] = "error"
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_BATCH_RULE"
        )

    def test_duplicate_rule_id(self):
        self.assert_error(
            run_json(payload([], [UNIQUE_RULE, UNIQUE_RULE])),
            "INVALID_BATCH_RULE",
        )

    def test_unsupported_type(self):
        bad = {"id": "u", "type": "unique", "options": {"field": "x"}}
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_BATCH_RULE"
        )

    def test_unique_key_duplicate_fields(self):
        bad = {
            "id": "u",
            "type": "unique_key",
            "options": {"fields": ["a", "a"]},
        }
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_BATCH_RULE"
        )

    def test_group_ratio_bad_min_ratio(self):
        bad = {
            "id": "gr",
            "type": "group_ratio",
            "options": {
                "group_fields": ["g"],
                "value_field": "v",
                "allowed_values": ["ok"],
                "min_ratio": 2,
            },
        }
        self.assert_error(
            run_json(payload([], [bad])), "INVALID_BATCH_RULE"
        )

    def test_rule_error_takes_priority_over_record_error(self):
        bad_rule = {
            "id": "u",
            "type": "unique_key",
            "options": {"fields": []},
        }
        self.assert_error(
            run_json(payload([1], [bad_rule])), "INVALID_BATCH_RULE"
        )

    def test_error_output_is_a_single_line(self):
        proc = run_json(payload([1], []))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
