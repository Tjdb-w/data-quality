"""End-to-end tests for the ``dq field-impact`` command line interface."""

import copy
import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "field-impact"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def ref(dataset, field):
    return {"dataset": dataset, "field": field}


def edge(sd, sf, td, tf):
    return {
        "sourceDataset": sd,
        "sourceField": sf,
        "targetDataset": td,
        "targetField": tf,
    }


def rule(rule_id, status, fields, failed_ids):
    return {
        "ruleId": rule_id,
        "status": status,
        "fields": fields,
        "failedSampleIds": failed_ids,
    }


def sample(dataset, field_values):
    return {"dataset": dataset, "fieldValues": field_values}


# ods.name -> dwd.label -> ads.label; dwd.id is isolated.
BASE = {
    "datasets": {
        "ods": ["id", "name"],
        "dwd": ["id", "label"],
        "ads": ["label"],
    },
    "lineageEdges": [
        edge("ods", "name", "dwd", "label"),
        edge("dwd", "label", "ads", "label"),
    ],
    "validationResults": [
        rule(
            "r-fail",
            "failed",
            [ref("dwd", "label"), ref("ods", "name")],
            ["s1", "s2", "ghost"],
        ),
        rule("r-pass", "passed", [ref("ads", "label")], ["s3"]),
        rule("r-skip", "skipped", [ref("ads", "label")], ["s4"]),
        rule("r-isolated", "failed", [ref("dwd", "id")], ["s5"]),
    ],
    "anomalySamples": {
        "s1": sample("dwd", {"label": "x"}),
        "s2": sample("ads", {"label": "y"}),
        "s4": sample("ads", {"label": "z"}),
        "s5": sample("dwd", {"id": 0}),
    },
    "seedFields": [ref("ods", "name")],
}


class CliFieldImpactSuccessTest(unittest.TestCase):
    def test_basic_impact(self):
        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "status": "ok",
                "impacts": [
                    {
                        "seed": ref("ods", "name"),
                        "downstreamFields": [
                            ref("ads", "label"),
                            ref("dwd", "label"),
                        ],
                        "affectedRules": ["r-fail", "r-skip"],
                        "anomalySamples": ["s1", "s2", "s4"],
                        "paths": [
                            [
                                ref("ods", "name"),
                                ref("dwd", "label"),
                                ref("ads", "label"),
                            ],
                            [ref("ods", "name"), ref("dwd", "label")],
                        ],
                    }
                ],
                "unresolvedReferences": [],
            },
        )

    def test_result_matches_python_entry_point(self):
        from data_quality import analyze_field_impacts

        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, analyze_field_impacts(copy.deepcopy(BASE)))

    def test_unknown_seed_is_empty(self):
        payload = copy.deepcopy(BASE)
        payload["seedFields"] = [ref("ghost", "field"), ref("ods", "name")]
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["impacts"][0]["downstreamFields"], [])
        self.assertEqual(body["impacts"][0]["affectedRules"], [])
        self.assertEqual(body["impacts"][0]["anomalySamples"], [])
        self.assertEqual(body["impacts"][0]["paths"], [])
        self.assertEqual(
            body["impacts"][1]["downstreamFields"],
            [ref("ads", "label"), ref("dwd", "label")],
        )

    def test_empty_seeds_still_reports_dangling_edges(self):
        payload = copy.deepcopy(BASE)
        payload["seedFields"] = []
        payload["lineageEdges"].append(edge("nope", "x", "ods", "name"))
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["impacts"], [])
        self.assertEqual(
            body["unresolvedReferences"],
            [edge("nope", "x", "ods", "name")],
        )

    def test_self_loop_and_cycle_reachability(self):
        payload = {
            "datasets": {"a": ["x"], "b": ["y"]},
            "lineageEdges": [
                edge("a", "x", "b", "y"),
                edge("b", "y", "a", "x"),
            ],
            "validationResults": [
                rule("r1", "failed", [ref("a", "x"), ref("b", "y")], ["s1"])
            ],
            "anomalySamples": {"s1": sample("a", {})},
            "seedFields": [ref("a", "x")],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        impact = json.loads(proc.stdout.decode("utf-8"))["impacts"][0]
        self.assertEqual(
            impact["downstreamFields"], [ref("a", "x"), ref("b", "y")]
        )
        self.assertEqual(impact["affectedRules"], ["r1"])
        self.assertEqual(impact["anomalySamples"], ["s1"])

    def test_utf8_input_and_output(self):
        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Output is a UTF-8 JSON document on stdout; stderr stays empty.
        self.assertEqual(proc.stderr, b"")

        payload = copy.deepcopy(BASE)
        payload["anomalySamples"]["样本-1"] = sample("ods", {"name": "异常"})
        payload["validationResults"][0]["failedSampleIds"].append("样本-1")
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("样本-1".encode("utf-8"), proc.stdout)


class CliFieldImpactErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"datasets": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_IMPACT_INPUT")
        self._assert_error(run_json("datasets"), "INVALID_IMPACT_INPUT")
        self._assert_error(run_json(None), "INVALID_IMPACT_INPUT")
        self._assert_error(run_json(42), "INVALID_IMPACT_INPUT")

    def test_top_level_missing_and_extra_keys(self):
        for key in (
            "datasets",
            "lineageEdges",
            "validationResults",
            "anomalySamples",
            "seedFields",
        ):
            bad = copy.deepcopy(BASE)
            del bad[key]
            self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

    def test_nested_structure_errors(self):
        bad = copy.deepcopy(BASE)
        bad["datasets"] = []
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["datasets"] = {"ods": ["name", ""]}
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["lineageEdges"] = {}
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["lineageEdges"][0]["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["validationResults"] = {}
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["status"] = "PASSED"
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["fields"] = [{"dataset": "dwd"}]
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["failedSampleIds"] = [1]
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"] = []
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {"dataset": "dwd"}
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = {}
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = ["ods.name"]
        self._assert_error(run_json(bad), "INVALID_IMPACT_INPUT")

    def test_error_precedence(self):
        # Bad JSON beats a structurally malformed payload.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # A parseable but non-object payload is INVALID_IMPACT_INPUT, not
        # INVALID_JSON.
        self._assert_error(run_json([]), "INVALID_IMPACT_INPUT")

    def test_no_partial_result_on_error(self):
        bad = copy.deepcopy(BASE)
        del bad["seedFields"]
        proc = run_json(bad)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertNotIn("impacts", body)
        self.assertNotIn("status", body)


if __name__ == "__main__":
    unittest.main()
