"""End-to-end tests for the ``dq violation-origins`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "violation-origins"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def result(rule_id, dataset_id, field_id, sample_id, violated=True, value=None):
    return {
        "rule_id": rule_id,
        "dataset_id": dataset_id,
        "field_id": field_id,
        "sample_id": sample_id,
        "violated": violated,
        "value": value,
    }


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def path_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


BASE_PAYLOAD = {
    "results": [
        result("r1", "ods", "name", "样本-1", value="x"),
        result("r2", "dwd", "label", "样本-1", value="bad"),
        result("r3", "ads", "label", "样本-1", value="z"),
    ],
    "lineage": {
        "datasets": ["ods", "dwd", "ads"],
        "fields": {"ods": ["name"], "dwd": ["label"], "ads": ["label"]},
        "edges": [
            edge("dwd", "label", "ods", "name", "upstream"),
            edge("dwd", "label", "ads", "label", "downstream"),
        ],
    },
    "targets": [
        {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-1"}
    ],
}


class CliViolationOriginsSuccessTest(unittest.TestCase):
    def test_report_with_origin_and_impact_evidence(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "reports": [
                    {
                        "target": {
                            "dataset_id": "dwd",
                            "field_id": "label",
                            "sample_id": "样本-1",
                        },
                        "originEvidence": [
                            {
                                "rule_id": "r2",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-1",
                                "violated": True,
                                "value": "bad",
                                "path": [path_node("dwd", "label")],
                            },
                            {
                                "rule_id": "r1",
                                "dataset_id": "ods",
                                "field_id": "name",
                                "sample_id": "样本-1",
                                "violated": True,
                                "value": "x",
                                "path": [
                                    path_node("ods", "name"),
                                    path_node("dwd", "label"),
                                ],
                            },
                        ],
                        "impactEvidence": [
                            {
                                "rule_id": "r3",
                                "dataset_id": "ads",
                                "field_id": "label",
                                "sample_id": "样本-1",
                                "violated": True,
                                "value": "z",
                                "path": [
                                    path_node("dwd", "label"),
                                    path_node("ads", "label"),
                                ],
                            },
                        ],
                    }
                ]
            },
        )

    def test_empty_targets(self):
        payload = dict(BASE_PAYLOAD, targets=[])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, {"reports": []})

    def test_utf8_input_and_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("样本-1".encode("utf-8"), proc.stdout)


class CliViolationOriginsErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"results": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_ORIGIN_INPUT")
        self._assert_error(run_json("results"), "INVALID_ORIGIN_INPUT")

    def test_invalid_origin_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["results"]
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["lineage"]
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["targets"]
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"dataset_id": "dwd", "field_id": "label"}]
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"][0]["sample_id"] = ""
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["violated"] = "yes"
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")

    def test_unknown_origin_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_ORIGIN_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_ORIGIN_REFERENCE")

    def test_unknown_origin_target_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [
            {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-9"}
        ]
        self._assert_error(run_json(bad), "UNKNOWN_ORIGIN_TARGET")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][1]["violated"] = False
        bad["targets"] = [
            {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-1"}
        ]
        self._assert_error(run_json(bad), "UNKNOWN_ORIGIN_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed input beats unknown references and targets.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"] = "nope"
        bad["targets"] = [{"dataset_id": "g", "field_id": "g", "sample_id": "g"}]
        self._assert_error(run_json(bad), "INVALID_ORIGIN_INPUT")
        # Unknown references beat unknown targets.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        bad["targets"] = [{"dataset_id": "g", "field_id": "g", "sample_id": "g"}]
        self._assert_error(run_json(bad), "UNKNOWN_ORIGIN_REFERENCE")


if __name__ == "__main__":
    unittest.main()
