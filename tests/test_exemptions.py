"""Unit tests for manual violation exemptions."""

import copy
import unittest

from data_quality import (
    InvalidExemptionError,
    apply_violation_exemptions,
    validate,
)


def rule(rule_id, rule_type, **options):
    return {"id": rule_id, "type": rule_type, "options": dict(options)}


def exemption(
    exemption_id="e1",
    rule_id="age-range",
    record_index=0,
    record_id="r1",
    field="age",
    reason="known legacy value",
):
    return {
        "exemption_id": exemption_id,
        "rule_id": rule_id,
        "record_index": record_index,
        "record_id": record_id,
        "field": field,
        "reason": reason,
    }


def validated_result(records=None, rules=None):
    if records is None:
        records = [
            {"id": "r1", "age": 200},
            {"id": "r2", "age": 50},
            {"id": "r3", "age": 7},
        ]
    if rules is None:
        rules = [rule("age-range", "range", field="age", min=0, max=120)]
    return validate(records, rules)


class ApplyExemptionsSuccessTest(unittest.TestCase):
    def test_partitions_violations_and_reports_counts(self):
        result = validated_result()
        report = apply_violation_exemptions(result, [exemption()])
        self.assertEqual(
            report["summary"],
            {
                "input_violation_count": 1,
                "active_violation_count": 0,
                "waived_violation_count": 1,
                "exemption_count": 1,
            },
        )
        self.assertEqual(report["active_violations"], [])
        waived = report["waived_violations"]
        self.assertEqual(len(waived), 1)
        self.assertEqual(
            waived[0],
            {
                "rule_id": "age-range",
                "record_index": 0,
                "record_id": "r1",
                "field": "age",
                "value": 200,
                "message": "is out of the allowed range",
                "exemption_id": "e1",
                "reason": "known legacy value",
            },
        )

    def test_status_ok_and_pass_condition_is_active_empty(self):
        result = validated_result(
            records=[
                {"id": "r1", "age": 200},
                {"id": "r2", "age": 999},
            ]
        )
        self.assertEqual(len(result["violations"]), 2)

        fully_waived = apply_violation_exemptions(
            result,
            [
                exemption("e1", record_index=0, record_id="r1"),
                exemption("e2", record_index=1, record_id="r2"),
            ],
        )
        self.assertEqual(fully_waived["status"], "ok")
        self.assertEqual(fully_waived["active_violations"], [])

        partially_waived = apply_violation_exemptions(
            result, [exemption("e1", record_index=0, record_id="r1")]
        )
        self.assertEqual(partially_waived["status"], "ok")
        self.assertEqual(
            [v["record_index"] for v in partially_waived["active_violations"]],
            [1],
        )

    def test_original_violations_keep_their_order_and_contents(self):
        result = validate(
            [
                {"id": "r1", "age": 999},
                {"id": "r2", "age": 5},
                {"id": "r3", "age": 999},
            ],
            [rule("age-range", "range", field="age", min=0, max=120)],
        )
        report = apply_violation_exemptions(
            result, [exemption("e1", record_index=2, record_id="r3")]
        )
        self.assertEqual(
            [v["record_index"] for v in report["active_violations"]], [0]
        )
        self.assertEqual(
            [v["record_index"] for v in report["waived_violations"]], [2]
        )
        # The input result is untouched and active entries are the same
        # objects; waived entries gain evidence without mutation.
        self.assertIs(
            report["active_violations"][0], result["violations"][0]
        )
        self.assertNotIn("exemption_id", result["violations"][1])
        self.assertEqual(
            set(report["waived_violations"][0]),
            {
                "rule_id",
                "record_index",
                "record_id",
                "field",
                "value",
                "message",
                "exemption_id",
                "reason",
            },
        )

    def test_input_result_is_not_mutated(self):
        result = validated_result()
        before = copy.deepcopy(result)
        apply_violation_exemptions(result, [exemption()])
        self.assertEqual(result, before)

    def test_empty_exemptions_keep_every_violation_active(self):
        result = validated_result()
        report = apply_violation_exemptions(result, [])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(
            report["summary"],
            {
                "input_violation_count": 1,
                "active_violation_count": 1,
                "waived_violation_count": 0,
                "exemption_count": 0,
            },
        )
        self.assertEqual(report["active_violations"], result["violations"])
        self.assertEqual(report["waived_violations"], [])

    def test_record_id_null_matches(self):
        result = validate(
            [{"age": 200}, {"age": 5}],
            [rule("age-range", "range", field="age", min=0, max=120)],
        )
        report = apply_violation_exemptions(
            result, [exemption(record_index=0, record_id=None)]
        )
        self.assertEqual(report["active_violations"], [])
        self.assertEqual(
            report["waived_violations"][0]["record_id"], None
        )

    def test_composite_results_are_not_considered(self):
        # Only single-field violations exist in the partition; the
        # exemption function never inspects composite results.
        result = validated_result()
        result["composite_results"] = {"status": "failed", "results": []}
        report = apply_violation_exemptions(result, [exemption()])
        self.assertNotIn("composite_results", report)
        self.assertEqual(report["waived_violations"][0]["field"], "age")


class ApplyExemptionsInputErrorTest(unittest.TestCase):
    def assert_invalid(self, result, exemptions):
        with self.assertRaises(InvalidExemptionError) as ctx:
            apply_violation_exemptions(result, exemptions)
        self.assertEqual(ctx.exception.code, "INVALID_EXEMPTION_INPUT")
        self.assertTrue(issubclass(InvalidExemptionError, ValueError))

    def test_result_must_be_validate_result(self):
        good_exemption = [exemption()]
        self.assert_invalid(None, good_exemption)
        self.assert_invalid({}, good_exemption)
        self.assert_invalid({"violations": None}, good_exemption)
        self.assert_invalid({"passed": False}, good_exemption)

    def test_exemptions_must_be_list(self):
        result = validated_result()
        self.assert_invalid(result, {})
        self.assert_invalid(result, "nope")

    def test_exemption_must_be_object(self):
        self.assert_invalid(validated_result(), ["nope"])

    def test_exemption_key_set_must_match_exactly(self):
        result = validated_result()
        bad = exemption()
        del bad["reason"]
        self.assert_invalid(result, [bad])
        bad = exemption()
        bad["extra"] = 1
        self.assert_invalid(result, [bad])

    def test_exemption_id_must_be_non_empty_string(self):
        result = validated_result()
        self.assert_invalid(result, [exemption(exemption_id="")])
        self.assert_invalid(result, [exemption(exemption_id=1)])
        self.assert_invalid(result, [exemption(exemption_id=None)])

    def test_rule_id_must_be_string(self):
        self.assert_invalid(validated_result(), [exemption(rule_id=1)])

    def test_record_index_must_be_non_negative_integer(self):
        result = validated_result()
        self.assert_invalid(result, [exemption(record_index=-1)])
        self.assert_invalid(result, [exemption(record_index="0")])
        self.assert_invalid(result, [exemption(record_index=1.0)])
        # Booleans are not integers in JSON input.
        self.assert_invalid(result, [exemption(record_index=True)])

    def test_record_id_must_be_string_or_null(self):
        result = validated_result()
        self.assert_invalid(result, [exemption(record_id=1)])
        self.assert_invalid(result, [exemption(record_id=True)])

    def test_field_must_be_string(self):
        self.assert_invalid(validated_result(), [exemption(field=3)])

    def test_reason_must_be_non_empty_string(self):
        result = validated_result()
        self.assert_invalid(result, [exemption(reason="")])
        self.assert_invalid(result, [exemption(reason=None)])

    def test_duplicate_exemption_id(self):
        result = validate(
            [
                {"id": "r1", "age": 999},
                {"id": "r2", "age": 999},
            ],
            [rule("age-range", "range", field="age", min=0, max=120)],
        )
        self.assert_invalid(
            result,
            [
                exemption("same", record_index=0, record_id="r1"),
                exemption("same", record_index=1, record_id="r2"),
            ],
        )

    def test_duplicate_target_same_reason(self):
        result = validated_result()
        self.assert_invalid(
            result,
            [exemption("e1"), exemption("e2", reason="known legacy value")],
        )

    def test_conflicting_target_different_reason(self):
        result = validated_result()
        self.assert_invalid(
            result,
            [exemption("e1"), exemption("e2", reason="a different reason")],
        )

    def test_unmatched_wrong_rule_id(self):
        self.assert_invalid(
            validated_result(), [exemption(rule_id="other-rule")]
        )

    def test_unmatched_wrong_record_index(self):
        self.assert_invalid(
            validated_result(),
            [exemption(record_index=1, record_id="r2")],
        )

    def test_unmatched_wrong_record_id(self):
        self.assert_invalid(
            validated_result(), [exemption(record_id="someone-else")]
        )

    def test_unmatched_record_id_null_does_not_match_string(self):
        result = validate(
            [{"id": "r1", "age": 200}],
            [rule("age-range", "range", field="age", min=0, max=120)],
        )
        self.assert_invalid(result, [exemption(record_id=None)])

    def test_unmatched_wrong_field(self):
        self.assert_invalid(validated_result(), [exemption(field="name")])

    def test_all_exemptions_validated_before_partition(self):
        # A valid exemption followed by an invalid one aborts the run;
        # the error is an exemption error, not a partial partition.
        result = validated_result()
        with self.assertRaises(InvalidExemptionError):
            apply_violation_exemptions(
                result,
                [
                    exemption("e1"),
                    exemption("e2", record_index=9, record_id="rx"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
