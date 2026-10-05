"""Unit tests for violation exemptions over validate results."""

import unittest

from data_quality import validate
from data_quality.exemptions import (
    InvalidExemptionError,
    apply_violation_exemptions,
)


RULES = [
    {
        "id": "age-range",
        "type": "range",
        "options": {"field": "age", "min": 0, "max": 120},
    },
    {
        "id": "name-required",
        "type": "required",
        "options": {"field": "name"},
    },
]

RECORDS = [
    {"id": "r1", "age": 200, "name": "a"},
    {"id": "r2", "age": 5},
    {"age": 7, "name": None},
]


def exemption(
    exemption_id="ex1",
    rule_id="age-range",
    record_index=0,
    record_id="r1",
    field="age",
    reason="known outlier",
):
    return {
        "exemption_id": exemption_id,
        "rule_id": rule_id,
        "record_index": record_index,
        "record_id": record_id,
        "field": field,
        "reason": reason,
    }


def result():
    return validate(RECORDS, RULES)


class ApplyExemptionsSuccessTest(unittest.TestCase):
    def test_no_exemptions_keeps_everything_active(self):
        violations = result()["violations"]
        out = apply_violation_exemptions(result(), [])
        self.assertEqual(out["status"], "violations")
        self.assertEqual(
            out["summary"],
            {
                "input_violation_count": len(violations),
                "active_violation_count": len(violations),
                "waived_violation_count": 0,
                "exemption_count": 0,
            },
        )
        self.assertEqual(out["active_violations"], violations)
        self.assertEqual(out["waived_violations"], [])

    def test_waive_one_violation(self):
        out = apply_violation_exemptions(result(), [exemption()])
        self.assertEqual(out["status"], "violations")
        summary = out["summary"]
        self.assertEqual(summary["input_violation_count"], 3)
        self.assertEqual(summary["active_violation_count"], 2)
        self.assertEqual(summary["waived_violation_count"], 1)
        self.assertEqual(summary["exemption_count"], 1)
        self.assertEqual(
            summary["input_violation_count"],
            summary["active_violation_count"]
            + summary["waived_violation_count"],
        )
        self.assertEqual(
            [v["record_index"] for v in out["active_violations"]], [1, 2]
        )
        waived = out["waived_violations"]
        self.assertEqual(len(waived), 1)
        self.assertEqual(waived[0]["exemption_id"], "ex1")
        self.assertEqual(waived[0]["reason"], "known outlier")
        self.assertEqual(waived[0]["value"], 200)
        self.assertEqual(waived[0]["message"], "is out of the allowed range")

    def test_waive_all_yields_status_ok(self):
        exemptions = [
            exemption(),
            exemption("ex2", "name-required", 1, "r2", "name", "legacy"),
            exemption("ex3", "name-required", 2, None, "name", "import"),
        ]
        out = apply_violation_exemptions(result(), exemptions)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["active_violations"], [])
        self.assertEqual(
            [w["exemption_id"] for w in out["waived_violations"]],
            ["ex1", "ex2", "ex3"],
        )
        self.assertEqual(out["summary"]["exemption_count"], 3)

    def test_original_order_is_preserved(self):
        # Violations are produced rule by rule; waiving the first one
        # must not reorder the remaining active ones.
        out = apply_violation_exemptions(result(), [exemption()])
        self.assertEqual(
            [(v["rule_id"], v["record_index"]) for v in out["active_violations"]],
            [("name-required", 1), ("name-required", 2)],
        )

    def test_input_result_is_not_mutated(self):
        res = result()
        apply_violation_exemptions(res, [exemption()])
        self.assertNotIn("exemption_id", res["violations"][0])
        self.assertNotIn("reason", res["violations"][0])

    def test_composite_results_are_not_included(self):
        res = validate(
            [{"id": "r1", "a": 1, "b": 2}],
            [{"id": "a-required", "type": "required", "options": {"field": "a"}}],
            dataset="ds",
            composite_rules=[
                {
                    "rule_id": "c1",
                    "severity": "error",
                    "fields": ["a", "b"],
                    "conditions": [
                        {
                            "type": "field_equal",
                            "left_field": "a",
                            "right_field": "b",
                        }
                    ],
                }
            ],
        )
        self.assertIn("composite_results", res)
        out = apply_violation_exemptions(res, [])
        self.assertNotIn("composite_results", out)
        self.assertEqual(out["status"], "ok")


class ApplyExemptionsErrorTest(unittest.TestCase):
    def assert_invalid(self, res, exemptions):
        with self.assertRaises(InvalidExemptionError) as ctx:
            apply_violation_exemptions(res, exemptions)
        self.assertEqual(ctx.exception.code, "INVALID_EXEMPTION_INPUT")
        self.assertIsInstance(ctx.exception, ValueError)

    def test_result_must_be_object(self):
        self.assert_invalid([], [])

    def test_result_must_have_violations_list(self):
        self.assert_invalid({"violations": {}}, [])
        self.assert_invalid({}, [])

    def test_result_violation_must_have_match_keys(self):
        self.assert_invalid({"violations": [{"rule_id": "r"}]}, [])

    def test_exemptions_must_be_a_list(self):
        self.assert_invalid(result(), {})
        self.assert_invalid(result(), "ex1")

    def test_exemption_must_be_object_with_exact_keys(self):
        self.assert_invalid(result(), [[]])
        bad = exemption()
        del bad["reason"]
        self.assert_invalid(result(), [bad])
        extra = exemption()
        extra["note"] = "x"
        self.assert_invalid(result(), [extra])

    def test_exemption_field_types(self):
        bad_id = exemption(exemption_id="")
        self.assert_invalid(result(), [bad_id])
        bad_index = exemption(record_index=-1)
        self.assert_invalid(result(), [bad_index])
        bad_index_bool = exemption(record_index=True)
        self.assert_invalid(result(), [bad_index_bool])
        bad_record_id = exemption(record_id=1)
        self.assert_invalid(result(), [bad_record_id])
        bad_reason = exemption(reason="")
        self.assert_invalid(result(), [bad_reason])
        bad_rule = exemption(rule_id=1)
        self.assert_invalid(result(), [bad_rule])
        bad_field = exemption(field=None)
        self.assert_invalid(result(), [bad_field])

    def test_duplicate_exemption_id(self):
        self.assert_invalid(
            result(),
            [
                exemption("ex1"),
                exemption("ex1", "name-required", 1, "r2", "name", "dup"),
            ],
        )

    def test_conflicting_exemptions_for_same_violation(self):
        self.assert_invalid(
            result(),
            [exemption("ex1"), exemption("ex2", reason="other")],
        )

    def test_unmatched_exemption(self):
        self.assert_invalid(result(), [exemption(record_index=1)])
        self.assert_invalid(
            result(), [exemption(rule_id="name-required", field="name")]
        )
        self.assert_invalid(result(), [exemption(record_id="r2")])
        self.assert_invalid(result(), [exemption(record_id=None)])


if __name__ == "__main__":
    unittest.main()
