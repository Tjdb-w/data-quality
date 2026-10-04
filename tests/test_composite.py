"""Tests for cross-field (composite) consistency rules."""

import unittest

from data_quality import (
    CompositeRuleError,
    FAILED,
    InvalidRecordReferenceError,
    PASSED,
    SKIPPED_MISSING_FIELD,
    SEVERITIES,
    analyze_field_impacts,
    composite_impact_inputs,
    composite_sample_id,
    evaluate_composite_rules,
    query_composite_results,
    register_composite_rules,
    validate,
)

DATASET = {
    "dataset_id": "ds",
    "fields": {
        "a": "string",
        "b": "string",
        "c": "string",
        "start": "date",
        "end": "date",
        "kind": "string",
        "closed_at": "datetime",
    },
}


def composite_rule(
    rule_id, fields, conditions, severity="error"
):
    return {
        "rule_id": rule_id,
        "fields": fields,
        "severity": severity,
        "conditions": conditions,
    }


def eq(rule_id, left="a", right="b", severity="error"):
    return composite_rule(
        rule_id, [left, right],
        [{"type": "field_equals", "left": left, "right": right}],
        severity=severity,
    )


class RegistrationTest(unittest.TestCase):
    def test_all_four_condition_types_compile(self):
        rules = [
            eq("r-eq"),
            composite_rule(
                "r-ne", ["a", "b"],
                [{"type": "field_not_equals", "left": "a", "right": "b"}],
                severity="warning",
            ),
            composite_rule(
                "r-date", ["start", "end"],
                [{"type": "date_order", "left": "start", "right": "end"}],
                severity="info",
            ),
            composite_rule(
                "r-req", ["kind", "closed_at"],
                [{"type": "required_if", "when": "kind", "field": "closed_at"}],
            ),
        ]
        compiled = register_composite_rules(DATASET, rules)
        self.assertEqual([rule["rule_id"] for rule in compiled],
                         ["r-eq", "r-ne", "r-date", "r-req"])
        self.assertEqual(compiled[0]["fields"], ["a", "b"])
        self.assertEqual(compiled[2]["severity"], "info")
        # Field declaration order is preserved.
        self.assertEqual(compiled[3]["fields"], ["kind", "closed_at"])

    def test_list_schema_is_accepted_without_type_checks(self):
        dataset = {"dataset_id": "ds", "fields": ["start", "end"]}
        compiled = register_composite_rules(
            dataset,
            [composite_rule(
                "r", ["start", "end"],
                [{"type": "date_order", "left": "start", "right": "end"}])],
        )
        self.assertEqual(compiled[0]["dataset_id"], "ds")

    def test_and_combination_must_cover_exactly_declared_fields(self):
        rule = composite_rule(
            "r", ["a", "b", "c"],
            [
                {"type": "field_equals", "left": "a", "right": "b"},
                {"type": "field_equals", "left": "b", "right": "c"},
            ],
        )
        compiled = register_composite_rules(DATASET, [rule])
        self.assertEqual(len(compiled[0]["conditions"]), 2)

    def test_empty_rule_set(self):
        for rules in ([], None, {}):
            with self.assertRaises(CompositeRuleError) as ctx:
                register_composite_rules(DATASET, rules)
            self.assertEqual(ctx.exception.code, "INVALID_RULE_SET")

    def test_duplicate_rule_id(self):
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [eq("dup"), eq("dup")])
        self.assertEqual(ctx.exception.code, "DUPLICATE_RULE_ID")
        self.assertEqual(ctx.exception.rule_id, "dup")

    def test_unknown_severity(self):
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [eq("r", severity="fatal")])
        self.assertEqual(ctx.exception.code, "INVALID_SEVERITY")
        self.assertEqual(ctx.exception.rule_id, "r")

    def test_unsupported_condition(self):
        rule = composite_rule(
            "r", ["a", "b"],
            [{"type": "crosses_zero", "left": "a", "right": "b"}],
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_COMPOSITE_CONDITION")
        self.assertEqual(ctx.exception.rule_id, "r")

    def test_unknown_field_is_rejected_with_rule_and_field(self):
        rule = composite_rule(
            "r", ["a", "ghost"],
            [{"type": "field_equals", "left": "a", "right": "ghost"}],
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.rule_id, "r")
        self.assertEqual(ctx.exception.field, "ghost")

    def test_self_reference_is_rejected(self):
        rule = composite_rule(
            "r", ["a"], [{"type": "field_equals", "left": "a", "right": "a"}]
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.field, "a")

        rule = composite_rule(
            "r2", ["a"], [{"type": "required_if", "when": "a", "field": "a"}]
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")

    def test_non_date_field_in_date_order_is_rejected(self):
        rule = composite_rule(
            "r", ["a", "start"],
            [{"type": "date_order", "left": "a", "right": "start"}],
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.field, "a")

    def test_invalid_date_format_is_rejected(self):
        rule = composite_rule(
            "r", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end",
              "format": "%Y-%Q-%d"}],
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.code, "INVALID_COMPOSITE_RULE")
        self.assertEqual(ctx.exception.field, "start")

        dangling = composite_rule(
            "r2", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end",
              "format": "%Y%"}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [dangling])

    def test_valid_date_format_compiles(self):
        rule = composite_rule(
            "r", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end",
              "format": "%d/%m/%Y"}],
        )
        compiled = register_composite_rules(DATASET, [rule])
        self.assertEqual(compiled[0]["conditions"][0]["format"], "%d/%m/%Y")

    def test_declared_fields_must_match_conditions(self):
        # Declared but never referenced.
        rule = composite_rule(
            "r", ["a", "b", "c"],
            [{"type": "field_equals", "left": "a", "right": "b"}],
        )
        with self.assertRaises(CompositeRuleError) as ctx:
            register_composite_rules(DATASET, [rule])
        self.assertEqual(ctx.exception.field, "c")

        # Referenced but not declared in fields.
        rule = composite_rule(
            "r", ["a"], [{"type": "field_equals", "left": "a", "right": "b"}]
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])

    def test_duplicate_target_field_rejected(self):
        rule = composite_rule(
            "r", ["a", "a"],
            [{"type": "field_equals", "left": "a", "right": "b"}],
        )
        # fields reference b too, so the duplicate a is caught first.
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])

    def test_unknown_rule_and_condition_keys_rejected(self):
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(
                DATASET, [dict(eq("r"), extra=1)]
            )
        rule = composite_rule(
            "r2", ["a", "b"],
            [{"type": "field_equals", "left": "a", "right": "b",
              "loose": True}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])

    def test_condition_keys_are_checked_by_type(self):
        # Comparison conditions reject when/field.
        rule = composite_rule(
            "r", ["a", "b"],
            [{"type": "field_equals", "left": "a", "right": "b",
              "when": "a"}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])
        # field_equals does not accept format.
        rule = composite_rule(
            "r2", ["a", "b"],
            [{"type": "field_not_equals", "left": "a", "right": "b",
              "format": "%Y"}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])
        # required_if rejects left/right.
        rule = composite_rule(
            "r3", ["kind", "closed_at"],
            [{"type": "required_if", "when": "kind", "field": "closed_at",
              "left": "kind"}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [rule])

    def test_bad_batch_is_not_partially_registered(self):
        good = eq("good")
        bad = composite_rule(
            "bad", ["a", "ghost"],
            [{"type": "field_equals", "left": "a", "right": "ghost"}],
        )
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(DATASET, [good, bad])
        # The good rule alone is still fine, proving the failure was atomic.
        self.assertEqual(
            register_composite_rules(DATASET, [good])[0]["rule_id"], "good"
        )

    def test_dataset_descriptor_validated(self):
        with self.assertRaises(CompositeRuleError):
            register_composite_rules({}, [eq("r")])
        with self.assertRaises(CompositeRuleError):
            register_composite_rules(
                {"dataset_id": "", "fields": ["a"]}, [eq("r")]
            )


class EvaluationTest(unittest.TestCase):
    def _run(self, rules, records, dataset=DATASET):
        compiled = register_composite_rules(dataset, rules)
        return evaluate_composite_rules(records, compiled)

    def test_field_equals_uses_json_equality(self):
        out = self._run(
            [eq("r")],
            [
                {"id": 1, "a": 1, "b": 1},
                {"id": 2, "a": {"x": 1}, "b": {"x": 1}},
                {"id": 3, "a": 1, "b": True},
                {"id": 4, "a": "x", "b": "y"},
            ],
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]],
            [PASSED, PASSED, FAILED, FAILED],
        )

    def test_field_not_equals(self):
        rule = composite_rule(
            "r", ["a", "b"],
            [{"type": "field_not_equals", "left": "a", "right": "b"}],
        )
        out = self._run(
            [rule],
            [{"id": 1, "a": "x", "b": "y"}, {"id": 2, "a": "x", "b": "x"}],
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]], [PASSED, FAILED]
        )

    def test_date_ordering_and_equality(self):
        rule = composite_rule(
            "r", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end"}],
        )
        out = self._run(
            [rule],
            [
                {"id": "before", "start": "2024-01-01", "end": "2024-02-01"},
                {"id": "same", "start": "2024-01-01", "end": "2024-01-01"},
                {"id": "after", "start": "2024-03-01", "end": "2024-02-01"},
                {"id": "ts", "start": "2024-01-01T08:00:00",
                 "end": "2024-01-01T20:00:00"},
            ],
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]],
            [PASSED, PASSED, FAILED, PASSED],
        )

    def test_date_order_with_custom_format(self):
        dataset = {"dataset_id": "ds", "fields": ["start", "end"]}
        rule = composite_rule(
            "r", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end",
              "format": "%d/%m/%Y"}],
        )
        out = self._run(
            [rule],
            [
                {"id": 1, "start": "01/01/2024", "end": "02/01/2024"},
                {"id": 2, "start": "01/12/2024", "end": "02/01/2024"},
            ],
            dataset=dataset,
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]], [PASSED, FAILED]
        )

    def test_unparseable_date_value_fails_the_rule(self):
        rule = composite_rule(
            "r", ["start", "end"],
            [{"type": "date_order", "left": "start", "right": "end"}],
        )
        out = self._run(
            [rule], [{"id": 1, "start": "not-a-date", "end": "2024-01-01"}]
        )
        self.assertEqual(out["results"][0]["conclusion"], FAILED)

    def test_required_if_precondition(self):
        rule = composite_rule(
            "r", ["kind", "closed_at"],
            [{"type": "required_if", "when": "kind", "field": "closed_at"}],
        )
        out = self._run(
            [rule],
            [
                # Precondition false (null): requirement does not apply.
                {"id": 1, "kind": None, "closed_at": None},
                # Precondition false (absent key would skip; here null).
                {"id": 2, "kind": None, "closed_at": "2024-01-01T00:00:00"},
                # Precondition true, target null: violation.
                {"id": 3, "kind": "done", "closed_at": None},
                # Precondition true, target populated: passes.
                {"id": 4, "kind": "done", "closed_at": "2024-01-01T00:00:00"},
            ],
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]],
            [PASSED, PASSED, FAILED, PASSED],
        )

    def test_and_combination(self):
        rule = composite_rule(
            "r", ["a", "b", "c"],
            [
                {"type": "field_equals", "left": "a", "right": "b"},
                {"type": "field_equals", "left": "b", "right": "c"},
            ],
        )
        out = self._run(
            [rule],
            [
                {"id": 1, "a": 1, "b": 1, "c": 1},
                {"id": 2, "a": 1, "b": 1, "c": 2},
                {"id": 3, "a": 1, "b": 2, "c": 1},
            ],
        )
        condition_flags = [
            [c["satisfied"] for c in r["context"]["conditions"]]
            for r in out["results"]
        ]
        self.assertEqual(
            condition_flags, [[True, True], [True, False], [False, False]]
        )
        self.assertEqual(
            [r["conclusion"] for r in out["results"]],
            [PASSED, FAILED, FAILED],
        )

    def test_missing_field_skips_without_passing(self):
        rule = composite_rule(
            "r", ["a", "b"],
            [{"type": "field_equals", "left": "a", "right": "b"}],
        )
        out = self._run(
            [rule],
            [
                {"id": "full", "a": 1, "b": 2},
                {"id": "partial", "a": 1},
                {"id": "none"},
            ],
        )
        conclusions = [r["conclusion"] for r in out["results"]]
        self.assertEqual(conclusions, [FAILED, SKIPPED_MISSING_FIELD,
                                       SKIPPED_MISSING_FIELD])
        skipped = out["results"][1]
        self.assertEqual(skipped["context"]["missing_fields"], ["b"])
        self.assertIsNone(skipped["context"]["values"])

    def test_skip_does_not_block_other_rules(self):
        rules = [
            eq("r1"),
            composite_rule(
                "r2", ["a", "b"],
                [{"type": "field_not_equals", "left": "a", "right": "b"}]),
        ]
        out = self._run(rules, [{"id": 1, "a": 1}])
        self.assertEqual(
            {(r["rule_id"], r["conclusion"]) for r in out["results"]},
            {("r1", SKIPPED_MISSING_FIELD),
             ("r2", SKIPPED_MISSING_FIELD)},
        )

    def test_multiple_violations_per_record_are_all_kept_in_order(self):
        rules = [eq("r1"), eq("r2"), eq("r3")]
        records = [{"id": "x", "a": 1, "b": 2}, {"id": "y", "a": 3, "b": 4}]
        out = self._run(rules, records)
        # Rule-major order: registration order first, then record order.
        self.assertEqual(
            [(r["rule_id"], r["record_index"]) for r in out["results"]],
            [("r1", 0), ("r1", 1), ("r2", 0), ("r2", 1), ("r3", 0), ("r3", 1)],
        )
        self.assertTrue(all(r["conclusion"] == FAILED for r in out["results"]))

    def test_result_shape_is_complete(self):
        out = self._run([eq("r", severity="warning")],
                        [{"a": 1, "b": 1}])
        result = out["results"][0]
        self.assertEqual(
            set(result),
            {"dataset_id", "record_index", "record_id", "rule_id", "fields",
             "conclusion", "severity", "context"},
        )
        self.assertIsNone(result["record_id"])
        self.assertEqual(result["record_index"], 0)
        self.assertEqual(result["dataset_id"], "ds")
        self.assertEqual(result["fields"], ["a", "b"])
        self.assertEqual(result["severity"], "warning")
        self.assertEqual(result["context"]["values"], {"a": 1, "b": 1})
        self.assertEqual(
            result["context"]["conditions"][0]["fields"], ["a", "b"]
        )

    def test_summary_counts(self):
        out = self._run(
            [eq("r")],
            [{"id": 1, "a": 1, "b": 1}, {"id": 2, "a": 1}, {"id": 3, "a": 1, "b": 2}],
        )
        self.assertEqual(
            out["summary"],
            {
                "dataset_id": "ds",
                "record_count": 3,
                "rule_count": 1,
                "passed_count": 1,
                "failed_count": 1,
                "skipped_count": 1,
            },
        )
        self.assertEqual(out["dataset_id"], "ds")

    def test_invalid_record_reference(self):
        compiled = register_composite_rules(DATASET, [eq("r")])
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate_composite_rules({}, compiled)
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate_composite_rules([1], compiled)
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate_composite_rules([], [])
        with self.assertRaises(InvalidRecordReferenceError):
            evaluate_composite_rules([{"a": 1, "b": 1}], compiled,
                                     dataset_id="other")


class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules = [
            eq("r-err", severity="error"),
            composite_rule(
                "r-warn", ["a", "b"],
                [{"type": "field_not_equals", "left": "a", "right": "b"}],
                severity="warning"),
        ]
        compiled = register_composite_rules(DATASET, rules)
        cls.output = evaluate_composite_rules(
            [{"id": "x", "a": 1, "b": 2}, {"id": "y", "a": 1, "b": 1}],
            compiled,
        )

    def test_filter_by_rule_id(self):
        selected = query_composite_results(self.output, {"rule_id": "r-warn"})
        self.assertEqual({r["rule_id"] for r in selected}, {"r-warn"})
        self.assertEqual(len(selected), 2)

    def test_filter_by_severity_and_record(self):
        selected = query_composite_results(
            self.output, {"severity": "error", "record_id": "x"}
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["rule_id"], "r-err")
        self.assertEqual(selected[0]["conclusion"], FAILED)

        by_index = query_composite_results(self.output, {"record_index": 1})
        self.assertEqual({r["record_index"] for r in by_index}, {1})

    def test_list_filters(self):
        selected = query_composite_results(
            self.output, {"rule_id": ["r-err", "r-warn"],
                          "severity": ["error", "warning"]}
        )
        self.assertEqual(len(selected), 4)

    def test_filters_preserve_stable_order(self):
        selected = query_composite_results(self.output, {"severity": "warning"})
        self.assertEqual([r["record_index"] for r in selected], [0, 1])

    def test_bare_list_is_accepted(self):
        selected = query_composite_results(self.output["results"],
                                           {"rule_id": "r-err"})
        self.assertEqual(len(selected), 2)

    def test_invalid_filters(self):
        with self.assertRaises(InvalidRecordReferenceError):
            query_composite_results(self.output, {"severity": "fatal"})
        with self.assertRaises(InvalidRecordReferenceError):
            query_composite_results(self.output, {"record_index": -1})
        with self.assertRaises(InvalidRecordReferenceError):
            query_composite_results(self.output, {"unknown": 1})
        with self.assertRaises(InvalidRecordReferenceError):
            query_composite_results([{"nope": True}])


class DownstreamConsumptionTest(unittest.TestCase):
    def _output(self):
        rules = [
            eq("r1", severity="error"),
            composite_rule(
                "r2", ["start", "end"],
                [{"type": "date_order", "left": "start", "right": "end"}]),
        ]
        compiled = register_composite_rules(DATASET, rules)
        return evaluate_composite_rules(
            [
                {"id": "s1", "a": 1, "b": 2,
                 "start": "2024-03-01", "end": "2024-01-01"},
                {"start": "2024-01-01", "end": "2024-02-01"},
            ],
            compiled,
        )

    def test_adapter_builds_rules_and_samples(self):
        fragments = composite_impact_inputs(self._output())
        self.assertEqual(
            [rule["ruleId"] for rule in fragments["validationResults"]],
            ["r1", "r2"],
        )
        r1 = fragments["validationResults"][0]
        self.assertEqual(r1["status"], "failed")
        self.assertEqual(r1["failedSampleIds"], ["s1"])
        self.assertEqual(
            r1["fields"],
            [{"dataset": "ds", "field": "a"},
             {"dataset": "ds", "field": "b"}],
        )
        sample = fragments["anomalySamples"]["s1"]
        self.assertEqual(sample["dataset"], "ds")
        # Participating fields merge across every failed rule on the record.
        self.assertEqual(
            sample["fieldValues"],
            {"a": 1, "b": 2, "start": "2024-03-01", "end": "2024-01-01"},
        )

    def test_record_index_fallback_sample_id(self):
        compiled = register_composite_rules(DATASET, [eq("r")])
        output = evaluate_composite_rules([{"a": 1, "b": 2}], compiled)
        fragments = composite_impact_inputs(output)
        self.assertEqual(set(fragments["anomalySamples"]), {"record:0"})
        self.assertEqual(composite_sample_id(output["results"][0]), "record:0")

    def test_passed_and_skipped_are_not_anomalies(self):
        fragments = composite_impact_inputs(self._output())
        # Second record: r1 skips and r2 passes -> no anomaly sample.
        self.assertEqual(set(fragments["anomalySamples"]), {"s1"})
        self.assertTrue(all(
            rule["failedSampleIds"] == ["s1"]
            for rule in fragments["validationResults"]
        ))

    def test_anomaly_locator_and_lineage_consumed_by_impact_analysis(self):
        output = self._output()
        fragments = composite_impact_inputs(output)
        payload = {
            "datasets": {
                "ds": ["a", "b", "start", "end"],
                "ads": ["report_date"],
            },
            "lineageEdges": [
                {"sourceDataset": "ds", "sourceField": "end",
                 "targetDataset": "ads", "targetField": "report_date"},
                {"sourceDataset": "ds", "sourceField": "start",
                 "targetDataset": "ds", "targetField": "end"},
            ],
            "validationResults": fragments["validationResults"],
            "anomalySamples": fragments["anomalySamples"],
            "seedFields": [{"dataset": "ds", "field": "start"}],
        }
        result = analyze_field_impacts(payload)
        self.assertEqual(result["status"], "ok")
        impact = result["impacts"][0]
        # start -> ds.end -> ads.report_date along the existing directed
        # lineage edges, sorted by full field name.
        self.assertEqual(
            [(f["dataset"], f["field"]) for f in impact["downstreamFields"]],
            [("ads", "report_date"), ("ds", "end")],
        )
        # Only r2 (start/end) touches a downstream field; r1 (a/b) does not.
        self.assertEqual(impact["affectedRules"], ["r2"])
        self.assertEqual(impact["anomalySamples"], ["s1"])

        # Jumping back from an anomaly names the same record and every
        # participating field as the validation result.
        failed = [r for r in output["results"] if r["conclusion"] == FAILED]
        self.assertTrue(failed)
        sample = fragments["anomalySamples"]["s1"]
        for result_row in failed:
            self.assertEqual(composite_sample_id(result_row), "s1")
            self.assertEqual(result_row["record_id"], "s1")
            for name in result_row["fields"]:
                self.assertIn(name, sample["fieldValues"])


class ValidateIntegrationTest(unittest.TestCase):
    def test_single_run_keeps_both_result_kinds(self):
        single_rules = [
            {"id": "name-req", "type": "required", "options": {"field": "name"}}
        ]
        composite_rules = [
            composite_rule(
                "dates", ["start", "end"],
                [{"type": "date_order", "left": "start", "right": "end"}])
        ]
        records = [
            {"id": "r1", "name": "n", "start": "2024-01-01",
             "end": "2024-02-01"},
            {"id": "r2", "start": "2024-05-01", "end": "2024-01-01"},
        ]
        result = validate(
            records, single_rules,
            dataset={"dataset_id": "ds", "fields": ["start", "end", "name"]},
            composite_rules=composite_rules,
        )

        # Single-field results are byte-for-byte the baseline shape.
        baseline = validate(records, single_rules)
        self.assertEqual(result["passed"], baseline["passed"])
        self.assertEqual(result["summary"], baseline["summary"])
        self.assertEqual(result["violations"], baseline["violations"])
        self.assertEqual(result["violations"][0]["rule_id"], "name-req")

        composite = result["composite"]
        self.assertEqual(
            [(r["record_id"], r["conclusion"]) for r in composite["results"]],
            [("r1", PASSED), ("r2", FAILED)],
        )

    def test_baseline_without_composite_rules_is_unchanged(self):
        result = validate([{"id": 1}],
                          [{"id": "r", "type": "required",
                            "options": {"field": "x"}}])
        self.assertNotIn("composite", result)
        self.assertFalse(result["passed"])

    def test_registration_failure_aborts_before_records_are_read(self):
        with self.assertRaises(CompositeRuleError) as ctx:
            validate(
                "not-a-record-list",
                [{"id": "ok", "type": "required",
                  "options": {"field": "name"}}],
                dataset={"dataset_id": "ds", "fields": ["a", "b"]},
                composite_rules=[eq("dup"), eq("dup")],
            )
        self.assertEqual(ctx.exception.code, "DUPLICATE_RULE_ID")

    def test_severities_constant(self):
        self.assertEqual(SEVERITIES, ("error", "warning", "info"))


if __name__ == "__main__":
    unittest.main()
