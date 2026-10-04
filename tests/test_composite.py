"""Tests for cross-field composite consistency rules."""

import unittest

from data_quality import (
    CompositeRuleSetError,
    DuplicateRuleIdError,
    InvalidCompositeRuleError,
    InvalidInputError,
    InvalidRecordReferenceError,
    InvalidRuleError,
    InvalidSeverityError,
    UnsupportedCompositeConditionError,
    analyze_field_impacts,
    composite_results_to_impact_inputs,
    evaluate_composite_rules,
    query_composite_results,
    register_composite_rules,
    sample_id_for_record,
    trace_field_lineage,
    validate,
)


def crule(rule_id, fields, conditions, severity="error"):
    return {
        "rule_id": rule_id,
        "fields": list(fields),
        "conditions": list(conditions),
        "severity": severity,
    }


def eq(left, right):
    return {"type": "field_equal", "left_field": left, "right_field": right}


def neq(left, right):
    return {"type": "field_not_equal", "left_field": left, "right_field": right}


def before(earlier, later, **extra):
    condition = {
        "type": "date_before",
        "earlier_field": earlier,
        "later_field": later,
    }
    condition.update(extra)
    return condition


def req_when(required, when, equals=None, **extra):
    condition = {
        "type": "required_when",
        "when_field": when,
        "required_field": required,
        "equals": equals,
    }
    condition.update(extra)
    return condition


def register(rules, dataset="ds"):
    return register_composite_rules(dataset, rules)


def evaluate(records, rules, dataset="ds", record_refs=None):
    rule_set = register(rules, dataset)
    return evaluate_composite_rules(records, rule_set, record_refs)


def statuses(results):
    return [
        (r["record_index"], r["rule_id"], r["status"]) for r in results
    ]


class RegistrationTest(unittest.TestCase):
    VALID = crule("c1", ["a", "b"], [eq("a", "b")])

    def test_success_compiles_in_order(self):
        rule_set = register([
            crule("c2", ["a", "b"], [req_when("a", "b")]),
            self.VALID,
        ])
        self.assertEqual(rule_set["dataset_id"], "ds")
        self.assertEqual([r["rule_id"] for r in rule_set["rules"]], ["c2", "c1"])
        self.assertEqual(rule_set["rules"][1]["fields"], ["a", "b"])
        self.assertEqual(rule_set["rules"][1]["severity"], "error")

    def test_empty_rule_set(self):
        for bad in ([], None):
            with self.assertRaises(CompositeRuleSetError) as ctx:
                register(bad)
            self.assertEqual(ctx.exception.code, "INVALID_RULE_SET")
            self.assertIsNone(ctx.exception.rule_id)

    def test_rules_must_be_list_of_objects(self):
        with self.assertRaises(CompositeRuleSetError):
            register("nope")
        with self.assertRaises(CompositeRuleSetError):
            register(["nope"])

    def test_dataset_required_non_empty(self):
        with self.assertRaises(CompositeRuleSetError) as ctx:
            register_composite_rules(None, [self.VALID])
        self.assertEqual(ctx.exception.code, "INVALID_RULE_SET")
        with self.assertRaises(CompositeRuleSetError):
            register_composite_rules("", [self.VALID])

    def test_duplicate_rule_id(self):
        with self.assertRaises(DuplicateRuleIdError) as ctx:
            register([self.VALID, self.VALID])
        self.assertEqual(ctx.exception.code, "DUPLICATE_RULE_ID")
        self.assertEqual(ctx.exception.rule_id, "c1")

    def test_rule_id_must_be_non_empty_string(self):
        bad = dict(self.VALID, rule_id="")
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([bad])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.field_name, "rule_id")

    def test_unknown_severity(self):
        with self.assertRaises(InvalidSeverityError) as ctx:
            register([crule("c1", ["a", "b"], [eq("a", "b")], severity="fatal")])
        self.assertEqual(ctx.exception.code, "INVALID_SEVERITY")
        self.assertEqual(ctx.exception.rule_id, "c1")
        self.assertEqual(ctx.exception.field_name, "severity")

    def test_known_severities(self):
        for severity in ("error", "warning", "info"):
            rule_set = register(
                [crule("c", ["a", "b"], [eq("a", "b")], severity=severity)]
            )
            self.assertEqual(rule_set["rules"][0]["severity"], severity)

    def test_unsupported_condition_type(self):
        bad = crule("c1", ["a", "b"], [
            {"type": "magic", "left_field": "a", "right_field": "b"}
        ])
        with self.assertRaises(UnsupportedCompositeConditionError) as ctx:
            register([bad])
        self.assertEqual(
            ctx.exception.code, "UNSUPPORTED_COMPOSITE_CONDITION"
        )
        self.assertEqual(ctx.exception.rule_id, "c1")
        self.assertEqual(ctx.exception.field_name, "type")

    def test_unknown_field_is_invalid_composite_rule(self):
        # Condition references a field not declared in the rule's fields.
        bad = crule("c1", ["a"], [eq("a", "b")])
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([bad])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.rule_id, "c1")
        self.assertEqual(ctx.exception.field_name, "b")

    def test_self_reference_rejected(self):
        for condition in (
            eq("a", "a"),
            neq("a", "a"),
            before("a", "a"),
            req_when("a", "a"),
        ):
            with self.subTest(condition=condition):
                with self.assertRaises(InvalidCompositeRuleError) as ctx:
                    register([crule("c1", ["a"], [condition])])
                self.assertEqual(
                    ctx.exception.code, "INVALID_COMPOSITE_RULE"
                )
                self.assertEqual(ctx.exception.field_name, "a")

    def test_illegal_date_format_rejected_at_registration(self):
        bad = crule("c1", ["a", "b"], [before("a", "b", format="DD/MM/YYYY")])
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([bad])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.rule_id, "c1")
        self.assertEqual(ctx.exception.field_name, "format")

    def test_explicit_default_date_format_accepted(self):
        rule_set = register(
            [crule("c1", ["a", "b"], [before("a", "b", format="YYYY-MM-DD")])]
        )
        self.assertEqual(rule_set["rules"][0]["conditions"][0]["format"], "YYYY-MM-DD")

    def test_field_set_rules(self):
        # empty / non-list fields
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([{"rule_id": "c", "fields": [], "severity": "error",
                       "conditions": [eq("a", "b")]}])
        self.assertEqual(ctx.exception.field_name, "fields")
        with self.assertRaises(InvalidCompositeRuleError):
            register([{"rule_id": "c", "fields": "ab", "severity": "error",
                       "conditions": [eq("a", "b")]}])
        # duplicate field names
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([crule("c", ["a", "a"], [eq("a", "a")])])
        self.assertEqual(ctx.exception.field_name, "a")
        # non-string field
        with self.assertRaises(InvalidCompositeRuleError):
            register([{"rule_id": "c", "fields": [1], "severity": "error",
                       "conditions": []}])

    def test_conditions_must_be_non_empty_list(self):
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([crule("c", ["a", "b"], [])])
        self.assertEqual(ctx.exception.field_name, "conditions")

    def test_missing_and_unknown_rule_keys(self):
        with self.assertRaises(InvalidCompositeRuleError):
            register([{"rule_id": "c", "fields": ["a", "b"],
                       "conditions": [eq("a", "b")]}])
        with self.assertRaises(InvalidCompositeRuleError):
            register([dict(self.VALID, extra=1)])

    def test_unknown_condition_key(self):
        bad = crule("c", ["a", "b"], [
            {"type": "field_equal", "left_field": "a", "right_field": "b", "x": 1}
        ])
        with self.assertRaises(InvalidCompositeRuleError):
            register([bad])

    def test_missing_condition_reference(self):
        bad = {"rule_id": "c", "fields": ["a", "b"], "severity": "error",
               "conditions": [{"type": "field_equal", "left_field": "a"}]}
        with self.assertRaises(InvalidCompositeRuleError) as ctx:
            register([bad])
        self.assertEqual(ctx.exception.field_name, "right_field")

    def test_all_errors_are_value_errors(self):
        for error in (
            CompositeRuleSetError("x"),
            DuplicateRuleIdError("x"),
            InvalidSeverityError("x", "z"),
            InvalidCompositeRuleError("x", "f", "m"),
            InvalidRecordReferenceError("m"),
            UnsupportedCompositeConditionError("x", "z"),
        ):
            self.assertIsInstance(error, ValueError)

    def test_no_partial_registration(self):
        good = self.VALID
        bad = crule("bad", ["a"], [eq("a", "a")])
        with self.assertRaises(InvalidCompositeRuleError):
            register([good, bad])
        # The good rule is not silently usable: registration produced no
        # rule set, and registering it again still starts fresh.
        self.assertEqual(register([good])["rules"][0]["rule_id"], "c1")


class ConditionEvaluationTest(unittest.TestCase):
    def test_field_equal(self):
        rule = crule("c", ["a", "b"], [eq("a", "b")])
        results = evaluate(
            [{"id": "1", "a": 1, "b": 1},
             {"id": "2", "a": 1, "b": 2},
             {"id": "3", "a": {"x": [1]}, "b": {"x": [1]}},
             {"id": "4", "a": 1, "b": True}],
            [rule],
        )
        self.assertEqual(
            [r["status"] for r in results],
            ["PASSED", "FAILED", "PASSED", "FAILED"],
        )

    def test_field_not_equal(self):
        rule = crule("c", ["a", "b"], [neq("a", "b")])
        results = evaluate(
            [{"id": "1", "a": 1, "b": 1}, {"id": "2", "a": 1, "b": 2}],
            [rule],
        )
        self.assertEqual([r["status"] for r in results], ["FAILED", "PASSED"])

    def test_date_before_strict_ordering(self):
        rule = crule("c", ["d1", "d2"], [before("d1", "d2")])
        results = evaluate(
            [{"id": "1", "d1": "2026-01-01", "d2": "2026-01-02"},
             {"id": "2", "d1": "2026-01-02", "d2": "2026-01-01"},
             {"id": "3", "d1": "2026-01-01", "d2": "2026-01-01"},
             {"id": "4", "d1": "2026-13-01", "d2": "2026-01-02"},
             {"id": "5", "d1": "2026-01-01x", "d2": "2026-01-02"},
             {"id": "6", "d1": "not-a-date", "d2": "2026-01-02"}],
            [rule],
        )
        self.assertEqual(
            [r["status"] for r in results],
            ["PASSED", "FAILED", "FAILED", "FAILED", "FAILED", "FAILED"],
        )

    def test_required_when(self):
        rule = crule("c", ["kind", "closed"], [req_when("closed", "kind", equals="X")])
        results = evaluate(
            [{"id": "1", "kind": "X", "closed": "DONE"},
             {"id": "2", "kind": "X", "closed": None},
             {"id": "3", "kind": "Y", "closed": None},
             {"id": "4", "kind": "Y", "closed": "z"}],
            [rule],
        )
        self.assertEqual(
            [r["status"] for r in results],
            ["PASSED", "FAILED", "PASSED", "PASSED"],
        )

    def test_required_when_missing_target_field_is_skipped_not_passed(self):
        rule = crule("c", ["kind", "closed"], [req_when("closed", "kind", equals="X")])
        results = evaluate([{"id": "1", "kind": "Y"}], [rule])
        # Even though the prerequisite does not hold, a record missing the
        # required field is skipped rather than judged passed.
        self.assertEqual(results[0]["status"], "SKIPPED_MISSING_FIELD")

    def test_required_when_default_equals_null(self):
        rule = crule("c", ["parent", "child"], [
            {"type": "required_when", "when_field": "parent",
             "required_field": "child"}
        ])
        results = evaluate(
            [{"id": "1", "parent": None, "child": None},
             {"id": "2", "parent": None, "child": "x"},
             {"id": "3", "parent": "p", "child": None}],
            [rule],
        )
        self.assertEqual(
            [r["status"] for r in results], ["FAILED", "PASSED", "PASSED"]
        )

    def test_conditions_combine_with_and(self):
        rule = crule("c", ["a", "b", "d1", "d2"], [
            eq("a", "b"), before("d1", "d2"),
        ])
        results = evaluate(
            [{"id": "1", "a": 1, "b": 1, "d1": "2026-01-01", "d2": "2026-02-01"},
             {"id": "2", "a": 1, "b": 2, "d1": "2026-01-01", "d2": "2026-02-01"},
             {"id": "3", "a": 1, "b": 1, "d1": "2026-03-01", "d2": "2026-02-01"}],
            [rule],
        )
        self.assertEqual(
            [r["status"] for r in results], ["PASSED", "FAILED", "FAILED"]
        )
        # Context exposes every constituent condition's result.
        self.assertEqual(
            [(c["type"], c["satisfied"]) for c in results[1]["context"]["conditions"]],
            [("field_equal", False), ("date_before", True)],
        )


class MissingFieldTest(unittest.TestCase):
    def test_missing_target_field_skips_rule(self):
        rules = [
            crule("c1", ["a", "b"], [eq("a", "b")]),
            crule("c2", ["c", "d"], [req_when("c", "d", equals="X")]),
        ]
        results = evaluate(
            [
                {"id": "1", "a": 1, "b": 1, "c": "Y", "d": "Y"},
                {"id": "2", "c": "X", "d": "X"},  # missing a/b
            ],
            rules,
        )
        self.assertEqual(
            statuses(results),
            [
                (0, "c1", "PASSED"),
                (0, "c2", "PASSED"),
                (1, "c1", "SKIPPED_MISSING_FIELD"),
                (1, "c2", "PASSED"),
            ],
        )
        skipped = results[2]
        self.assertEqual(skipped["context"]["missing_fields"], ["a", "b"])
        self.assertNotIn("a", skipped["context"]["field_values"])
        self.assertIsNone(skipped["context"]["conditions"][0]["satisfied"])

    def test_skip_is_neither_pass_nor_fail(self):
        rule = crule("c", ["a", "b"], [req_when("a", "b")])
        results = evaluate([{"id": "1", "b": "X"}], [rule])
        self.assertEqual(results[0]["status"], "SKIPPED_MISSING_FIELD")


class OrderingAndShapeTest(unittest.TestCase):
    RULES = [
        crule("r1", ["a", "b"], [req_when("a", "b", equals=1)]),
        crule("r2", ["a", "b"], [eq("a", "b")]),
    ]
    RECORDS = [
        {"id": "x", "a": None, "b": 1},
        {"id": "y", "a": 2, "b": 1},
    ]

    def test_record_major_then_registration_order(self):
        results = evaluate(self.RECORDS, self.RULES)
        self.assertEqual(
            statuses(results),
            [
                (0, "r1", "FAILED"),
                (0, "r2", "FAILED"),
                (1, "r1", "PASSED"),
                (1, "r2", "FAILED"),
            ],
        )

    def test_multiple_violations_per_record_all_retained(self):
        results = evaluate(self.RECORDS, self.RULES)
        failed = [r for r in results if r["status"] == "FAILED"]
        self.assertEqual([(r["record_index"], r["rule_id"]) for r in failed],
                         [(0, "r1"), (0, "r2"), (1, "r2")])

    def test_result_shape(self):
        results = evaluate([{"a": 1, "b": 1}], self.RULES, dataset="ds-1")
        result = results[0]
        self.assertEqual(
            set(result),
            {"dataset_id", "record_index", "record_id", "rule_id", "fields",
             "status", "severity", "context"},
        )
        self.assertEqual(result["dataset_id"], "ds-1")
        self.assertEqual(result["record_index"], 0)
        self.assertIsNone(result["record_id"])  # no id field on the record
        self.assertEqual(result["fields"], ["a", "b"])
        self.assertEqual(set(result["context"]),
                         {"field_values", "missing_fields", "conditions"})
        self.assertEqual(result["context"]["field_values"], {"a": 1, "b": 1})

    def test_record_id_echoed(self):
        results = evaluate([{"id": "rec-9", "a": 1, "b": 1}], self.RULES)
        self.assertEqual(results[0]["record_id"], "rec-9")

    def test_stable_across_repeated_calls(self):
        first = evaluate(self.RECORDS, self.RULES)
        second = evaluate(self.RECORDS, self.RULES)
        self.assertEqual(first, second)

    def test_empty_records(self):
        self.assertEqual(evaluate([], self.RULES), [])


class RecordReferenceTest(unittest.TestCase):
    RULES = [crule("c", ["a", "b"], [eq("a", "b")])]
    RECORDS = [
        {"id": "r1", "a": 1, "b": 2},
        {"id": "r2", "a": 1, "b": 1},
        {"id": "r3", "a": 1, "b": 2},
    ]

    def test_filter_by_index(self):
        results = evaluate(self.RECORDS, self.RULES,
                           record_refs=[{"record_index": 2}])
        self.assertEqual(statuses(results), [(2, "c", "FAILED")])

    def test_filter_by_id(self):
        results = evaluate(self.RECORDS, self.RULES,
                           record_refs=[{"record_id": "r2"}])
        self.assertEqual(statuses(results), [(1, "c", "PASSED")])

    def test_index_and_id_must_agree(self):
        results = evaluate(
            self.RECORDS, self.RULES,
            record_refs=[{"record_index": 1, "record_id": "r2"}],
        )
        self.assertEqual(len(results), 1)
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(
                self.RECORDS, self.RULES,
                record_refs=[{"record_index": 0, "record_id": "r2"}],
            )

    def test_reference_order_preserved_and_deduped(self):
        results = evaluate(
            self.RECORDS, self.RULES,
            record_refs=[
                {"record_id": "r3"},
                {"record_index": 0},
                {"record_index": 2},
            ],
        )
        self.assertEqual([r["record_index"] for r in results], [2, 0])

    def test_out_of_range_index(self):
        with self.assertRaises(InvalidRecordReferenceError) as ctx:
            evaluate(self.RECORDS, self.RULES, record_refs=[{"record_index": 9}])
        self.assertEqual(ctx.exception.code, "INVALID_RECORD_REFERENCE")

    def test_unknown_record_id(self):
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(self.RECORDS, self.RULES,
                     record_refs=[{"record_id": "nope"}])

    def test_bad_reference_structure(self):
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(self.RECORDS, self.RULES, record_refs="nope")
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(self.RECORDS, self.RULES, record_refs=[{}])
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(self.RECORDS, self.RULES,
                     record_refs=[{"record_index": "1"}])
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate(self.RECORDS, self.RULES,
                     record_refs=[{"record_index": 0, "extra": 1}])

    def test_bad_records_structure(self):
        rule_set = register(self.RULES)
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate_composite_rules([1], rule_set)


class QueryTest(unittest.TestCase):
    def setUp(self):
        rules = [
            crule("c1", ["a", "b"], [eq("a", "b")], severity="error"),
            crule("c2", ["a", "b"], [neq("a", "b")], severity="warning"),
        ]
        self.results = evaluate(
            [{"id": "x", "a": 1, "b": 2}, {"id": "y", "a": 1, "b": 1}],
            rules,
        )

    def test_filter_by_rule_id(self):
        outcome = query_composite_results(self.results, rule_id="c1")
        self.assertEqual(outcome["count"], 2)
        self.assertTrue(all(r["rule_id"] == "c1" for r in outcome["results"]))

    def test_filter_by_severity(self):
        outcome = query_composite_results(self.results, severity="warning")
        self.assertEqual(
            [(r["record_index"], r["rule_id"]) for r in outcome["results"]],
            [(0, "c2"), (1, "c2")],
        )

    def test_filter_by_record_locator(self):
        outcome = query_composite_results(self.results, record_index=1)
        self.assertEqual([r["rule_id"] for r in outcome["results"]], ["c1", "c2"])
        outcome = query_composite_results(self.results, record_id="x")
        self.assertEqual([r["rule_id"] for r in outcome["results"]], ["c1", "c2"])

    def test_filters_combine_with_and_and_keep_order(self):
        outcome = query_composite_results(
            self.results, rule_id="c2", severity="warning", record_index=0
        )
        self.assertEqual(outcome["count"], 1)
        self.assertEqual(
            [(r["record_index"], r["rule_id"]) for r in outcome["results"]],
            [(0, "c2")],
        )

    def test_no_match(self):
        outcome = query_composite_results(self.results, rule_id="zzz")
        self.assertEqual(outcome, {"count": 0, "results": []})

    def test_query_does_not_change_historical_single_field_view(self):
        # Querying composite results never touches the single-field
        # violations; the filter output is a view in stable order.
        outcome = query_composite_results(self.results)
        self.assertEqual(
            statuses(outcome["results"]), statuses(self.results)
        )


class ValidateIntegrationTest(unittest.TestCase):
    SINGLE = [{"id": "s", "type": "required", "options": {"field": "a"}}]
    COMPOSITE = [
        crule("c1", ["a", "b"], [before("a", "b")], severity="error"),
        crule("c2", ["a", "b"], [req_when("a", "b")], severity="info"),
    ]
    RECORDS = [
        {"id": "r1", "a": "2026-01-01", "b": "2026-02-01"},
        {"id": "r2", "b": "2026-02-01"},
    ]

    def test_legacy_shape_unchanged_without_composite_rules(self):
        result = validate(self.RECORDS, self.SINGLE)
        self.assertEqual(set(result), {"passed", "summary", "violations"})
        self.assertEqual(result["summary"]["checked_rule_count"], 1)
        # The single-field failure sample content is untouched.
        self.assertEqual(result["violations"][0]["field"], "a")
        self.assertIsNone(result["violations"][0]["value"])

    def test_one_run_keeps_both_kinds_of_results(self):
        result = validate(
            self.RECORDS, self.SINGLE,
            dataset="ds", composite_rules=self.COMPOSITE,
        )
        # Single-field view unchanged.
        self.assertFalse(result["passed"])
        self.assertEqual(result["summary"]["violation_count"], 1)
        self.assertEqual(result["summary"]["checked_rule_count"], 1)
        self.assertEqual(len(result["violations"]), 1)
        # Composite results appended, complete and stable.
        self.assertEqual(result["composite_rule_count"], 2)
        self.assertEqual(
            statuses(result["composite_results"]),
            [
                (0, "c1", "PASSED"),
                (0, "c2", "PASSED"),
                (1, "c1", "SKIPPED_MISSING_FIELD"),
                (1, "c2", "SKIPPED_MISSING_FIELD"),
            ],
        )

    def test_dataset_required_with_composite_rules(self):
        with self.assertRaises(CompositeRuleSetError):
            validate(self.RECORDS, self.SINGLE, composite_rules=self.COMPOSITE)

    def test_registration_failure_aborts_whole_run(self):
        bad = crule("bad", ["a"], [eq("a", "a")])
        with self.assertRaises(InvalidCompositeRuleError):
            validate(
                self.RECORDS, self.SINGLE,
                dataset="ds", composite_rules=[self.COMPOSITE[0], bad],
            )

    def test_single_field_rule_errors_take_priority(self):
        with self.assertRaises(InvalidRuleError):
            validate(
                self.RECORDS,
                [{"id": "s", "type": "mystery", "options": {}}],
                dataset="ds", composite_rules=self.COMPOSITE,
            )

    def test_record_refs_propagate(self):
        result = validate(
            self.RECORDS, [], dataset="ds",
            composite_rules=self.COMPOSITE,
            record_refs=[{"record_id": "r2"}],
        )
        self.assertEqual([r["record_index"] for r in result["composite_results"]], [1, 1])

    def test_invalid_record_reference_surfaces_from_validate(self):
        with self.assertRaises(InvalidRecordReferenceError):
            validate(
                self.RECORDS, [], dataset="ds",
                composite_rules=self.COMPOSITE,
                record_refs=[{"record_id": "missing"}],
            )

    def test_bad_records_raise_existing_input_error(self):
        with self.assertRaises(InvalidInputError):
            validate([1], [], dataset="ds", composite_rules=self.COMPOSITE)


class DownstreamConsumptionTest(unittest.TestCase):
    def setUp(self):
        self.rules = [
            crule("date-order", ["start", "end"], [before("start", "end")],
                  severity="error"),
            crule("closed-when", ["kind", "closed"],
                  [req_when("closed", "kind", equals="X")],
                  severity="warning"),
        ]
        self.records = [
            {"id": "s1", "start": "2026-01-01", "end": "2026-02-01",
             "kind": "X", "closed": None},
            {"id": "s2", "start": "2026-03-01", "end": "2026-02-01",
             "kind": "X", "closed": "DONE"},
        ]
        self.results = evaluate(self.records, self.rules, dataset="dwd")

    def test_locator_round_trips_to_record_and_fields(self):
        failed = [r for r in self.results if r["status"] == "FAILED"]
        by_sample = {sample_id_for_record(r): r for r in failed}
        # s1 violates closed-when; s2 violates date-order.
        self.assertEqual(set(by_sample), {"s1", "s2"})
        r1 = by_sample["s1"]
        self.assertEqual(r1["rule_id"], "closed-when")
        record = next(r for r in self.records if r["id"] == "s1")
        self.assertEqual(r1["record_index"], self.records.index(record))
        for field in r1["fields"]:
            self.assertIn(field, record)
            self.assertEqual(
                r1["context"]["field_values"][field], record[field]
            )

    def test_impact_analysis_consumes_failed_results(self):
        adapted = composite_results_to_impact_inputs(self.results)
        # Rules appear in their first-failure order (stable evaluation
        # order: s1 fails closed-when before s2 fails date-order).
        self.assertEqual(
            [rule["ruleId"] for rule in adapted["validationResults"]],
            ["closed-when", "date-order"],
        )
        failed_rule = next(
            rule for rule in adapted["validationResults"]
            if rule["ruleId"] == "date-order"
        )
        self.assertEqual(failed_rule["status"], "failed")
        self.assertEqual(
            failed_rule["fields"],
            [{"dataset": "dwd", "field": "end"},
             {"dataset": "dwd", "field": "start"}],
        )
        self.assertEqual(failed_rule["failedSampleIds"], ["s2"])
        # Anomaly samples carry the same locator and participating values.
        self.assertEqual(
            adapted["anomalySamples"]["s2"]["fieldValues"]["end"], "2026-02-01"
        )

        payload = {
            "datasets": {
                "ods": ["raw_end"],
                "dwd": ["start", "end", "kind", "closed"],
            },
            "lineageEdges": [
                {"sourceDataset": "ods", "sourceField": "raw_end",
                 "targetDataset": "dwd", "targetField": "end"},
            ],
            "seedFields": [{"dataset": "ods", "field": "raw_end"}],
        }
        payload.update(adapted)
        impact = analyze_field_impacts(payload)
        item = impact["impacts"][0]
        self.assertIn("date-order", item["affectedRules"])
        self.assertNotIn("closed-when", item["affectedRules"])
        self.assertEqual(item["anomalySamples"], ["s2"])

    def test_sample_merges_fields_when_one_record_fails_several_rules(self):
        records = [
            {"id": "s9", "start": "2026-03-01", "end": "2026-02-01",
             "kind": "X", "closed": None},
        ]
        results = evaluate(records, self.rules, dataset="dwd")
        failed = [r for r in results if r["status"] == "FAILED"]
        self.assertEqual([r["rule_id"] for r in failed],
                         ["date-order", "closed-when"])
        adapted = composite_results_to_impact_inputs(results)
        sample = adapted["anomalySamples"]["s9"]
        # One locatable sample showing every participating field.
        self.assertEqual(
            set(sample["fieldValues"]),
            {"start", "end", "kind", "closed"},
        )

    def test_field_lineage_shows_associated_inputs_and_outputs(self):
        lineage = trace_field_lineage(
            {
                "ods": ["raw_end"],
                "dwd": ["start", "end"],
                "ads": ["report_end"],
            },
            [
                {"source": {"table": "ods", "column": "raw_end"},
                 "target": {"table": "dwd", "column": "end"}},
                {"source": {"table": "dwd", "column": "end"},
                 "target": {"table": "ads", "column": "report_end"}},
            ],
            {"table": "dwd", "column": "end"},
        )
        upstream = {
            (f["table"], f["column"]) for f in lineage["upstream"]["fields"]
        }
        downstream = {
            (f["table"], f["column"]) for f in lineage["downstream"]["fields"]
        }
        self.assertIn(("ods", "raw_end"), upstream)
        self.assertIn(("ads", "report_end"), downstream)


if __name__ == "__main__":
    unittest.main()
