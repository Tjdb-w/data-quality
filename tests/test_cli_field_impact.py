"""End-to-end tests for the ``dq field-impact`` command line interface."""

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


# ods.name -> dwd.label -> ads.label; dwd.id is isolated; one dangling edge.
BASE_PAYLOAD = {
    "datasets": {
        "ods": ["id", "name"],
        "dwd": ["id", "label"],
        "ads": ["label"],
    },
    "lineageEdges": [
        edge("ods", "name", "dwd", "label"),
        edge("dwd", "label", "ads", "label"),
        edge("ods", "name", "dwd", "label"),
        edge("ods", "ghost", "dwd", "label"),
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
    ],
    "anomalySamples": {
        "s1": sample("dwd", {"label": "x"}),
        "s2": sample("ads", {"label": "y"}),
        "s4": sample("ads", {"label": "z"}),
    },
    "seedFields": [ref("ods", "name")],
}


class CliFieldImpactSuccessTest(unittest.TestCase):
    def test_basic_impact(self):
        proc = run_json(BASE_PAYLOAD)
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
                "unresolvedReferences": [
                    edge("ods", "ghost", "dwd", "label"),
                ],
            },
        )

    def test_unknown_seed_and_empty_seeds(self):
        payload = dict(BASE_PAYLOAD, seedFields=[ref("ods", "missing")])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(len(body["impacts"]), 1)
        impact = body["impacts"][0]
        self.assertEqual(impact["seed"], ref("ods", "missing"))
        self.assertEqual(impact["downstreamFields"], [])
        self.assertEqual(impact["affectedRules"], [])
        self.assertEqual(impact["anomalySamples"], [])
        self.assertEqual(impact["paths"], [])

        payload = dict(BASE_PAYLOAD, seedFields=[])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["impacts"], [])
        # Dangling edges are still reported without seeds.
        self.assertEqual(
            body["unresolvedReferences"],
            [edge("ods", "ghost", "dwd", "label")],
        )

    def test_non_ascii_payload(self):
        payload = dict(
            BASE_PAYLOAD,
            datasets={"订单": ["编号"]},
            lineageEdges=[],
            validationResults=[],
            anomalySamples={},
            seedFields=[ref("订单", "编号")],
        )
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["impacts"][0]["seed"], ref("订单", "编号"))


class CliFieldImpactErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_invalid_utf8(self):
        proc = run_cli(b"\xff\xfe")
        self.assert_error(proc, "INVALID_JSON")

    def test_non_object_payload(self):
        proc = run_json([1, 2, 3])
        self.assert_error(proc, "INVALID_IMPACT_INPUT")

    def test_missing_top_level_key(self):
        payload = dict(BASE_PAYLOAD)
        del payload["seedFields"]
        self.assert_error(run_json(payload), "INVALID_IMPACT_INPUT")

    def test_unknown_top_level_key(self):
        payload = dict(BASE_PAYLOAD, extra=True)
        self.assert_error(run_json(payload), "INVALID_IMPACT_INPUT")

    def test_bad_rule_status(self):
        payload = dict(
            BASE_PAYLOAD,
            validationResults=[
                rule("r1", "broken", [ref("dwd", "label")], [])
            ],
        )
        self.assert_error(run_json(payload), "INVALID_IMPACT_INPUT")

    def test_bad_seed_reference(self):
        payload = dict(BASE_PAYLOAD, seedFields=[{"dataset": "ods"}])
        self.assert_error(run_json(payload), "INVALID_IMPACT_INPUT")


if __name__ == "__main__":
    unittest.main()
