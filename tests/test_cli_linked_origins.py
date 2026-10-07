"""End-to-end tests for the ``dq linked-origins`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "linked-origins"]


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


def field_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def sample_node(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": {"dataset": src_dataset, "field": src_field},
        "target": {"dataset": dst_dataset, "field": dst_field},
        "type": edge_type,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_node(left_dataset, left_sample),
        "right": sample_node(right_dataset, right_sample),
    }


BASE_PAYLOAD = {
    "results": [
        result("r0", "dwd", "label", "样本-b", value="bad"),
        result("r1", "ods", "name", "样本-a", value="x"),
        result("r2", "ads", "label", "样本-c", value="z"),
        result("r3", "dwd", "label", "样本-d", value="y"),
    ],
    "lineage": {
        "datasets": ["ods", "dwd", "ads"],
        "fields": {"ods": ["name"], "dwd": ["label"], "ads": ["label"]},
        "edges": [
            edge("dwd", "label", "ods", "name", "upstream"),
            edge("dwd", "label", "ads", "label", "downstream"),
        ],
    },
    "sample_links": [
        link("dwd", "样本-b", "ods", "样本-a"),
        link("dwd", "样本-b", "ads", "样本-c"),
        link("ods", "样本-a", "dwd", "样本-d"),
    ],
    "targets": [
        {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-b"}
    ],
}


class CliLinkedOriginsSuccessTest(unittest.TestCase):
    def test_full_report(self):
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
                            "sample_id": "样本-b",
                        },
                        "originEvidence": [
                            {
                                "rule_id": "r0",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-b",
                                "violated": True,
                                "value": "bad",
                                "sample_path": [
                                    sample_node("dwd", "样本-b"),
                                ],
                                "path": [field_node("dwd", "label")],
                            },
                            {
                                "rule_id": "r3",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-d",
                                "violated": True,
                                "value": "y",
                                "sample_path": [
                                    sample_node("dwd", "样本-b"),
                                    sample_node("ods", "样本-a"),
                                    sample_node("dwd", "样本-d"),
                                ],
                                "path": [field_node("dwd", "label")],
                            },
                            {
                                "rule_id": "r1",
                                "dataset_id": "ods",
                                "field_id": "name",
                                "sample_id": "样本-a",
                                "violated": True,
                                "value": "x",
                                "sample_path": [
                                    sample_node("dwd", "样本-b"),
                                    sample_node("ods", "样本-a"),
                                ],
                                "path": [
                                    field_node("ods", "name"),
                                    field_node("dwd", "label"),
                                ],
                            },
                        ],
                        "impactEvidence": [
                            {
                                "rule_id": "r3",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-d",
                                "violated": True,
                                "value": "y",
                                "sample_path": [
                                    sample_node("dwd", "样本-b"),
                                    sample_node("ods", "样本-a"),
                                    sample_node("dwd", "样本-d"),
                                ],
                                "path": [field_node("dwd", "label")],
                            },
                            {
                                "rule_id": "r2",
                                "dataset_id": "ads",
                                "field_id": "label",
                                "sample_id": "样本-c",
                                "violated": True,
                                "value": "z",
                                "sample_path": [
                                    sample_node("dwd", "样本-b"),
                                    sample_node("ads", "样本-c"),
                                ],
                                "path": [
                                    field_node("dwd", "label"),
                                    field_node("ads", "label"),
                                ],
                            },
                        ],
                    }
                ]
            },
        )

    def test_output_is_one_line(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(out.count("\n"), 1)

    def test_empty_targets(self):
        payload = dict(BASE_PAYLOAD, targets=[])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")), {"reports": []}
        )

    def test_duplicate_targets_duplicate_reports(self):
        payload = dict(
            BASE_PAYLOAD, targets=BASE_PAYLOAD["targets"] * 2
        )
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(len(body["reports"]), 2)
        self.assertEqual(body["reports"][0], body["reports"][1])

    def test_no_evidence_empty_lists(self):
        payload = {
            "results": [
                result("r0", "dwd", "label", "s1", value="bad"),
                result("r1", "ods", "name", "s2", value="x"),
            ],
            "lineage": BASE_PAYLOAD["lineage"],
            "sample_links": [],
            "targets": [
                {"dataset_id": "dwd", "field_id": "label", "sample_id": "s1"}
            ],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        report = body["reports"][0]
        self.assertEqual(len(report["originEvidence"]), 1)
        self.assertEqual(report["impactEvidence"], [])

    def test_utf8_input_and_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("样本-b".encode("utf-8"), proc.stdout)


class CliLinkedOriginsErrorTest(unittest.TestCase):
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
        self._assert_error(run_json([1, 2]), "INVALID_INPUT")
        self._assert_error(run_json("results"), "INVALID_INPUT")

    def test_top_level_keys_must_match_exactly(self):
        for key in ("results", "lineage", "sample_links", "targets"):
            bad = json.loads(json.dumps(BASE_PAYLOAD))
            del bad[key]
            self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_INPUT")

    def test_invalid_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"] = "nope"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["violated"] = "yes"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["lineage"]["edges"][0]["type"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = "nope"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"].append(
            link("dwd", "样本-b", "dwd", "样本-b")
        )
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [
            {"dataset_id": "dwd", "field_id": "label"}
        ]
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"][0]["sample_id"] = ""
        self._assert_error(run_json(bad), "INVALID_INPUT")

    def test_unknown_reference_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"][0]["right"]["sample_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

    def test_unknown_target_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [
            {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-x"}
        ]
        self._assert_error(run_json(bad), "UNKNOWN_TARGET")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["violated"] = False
        self._assert_error(run_json(bad), "UNKNOWN_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed input beats references and targets.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = "nope"
        bad["targets"] = [{"dataset_id": "g", "field_id": "g"}]
        self._assert_error(run_json(bad), "INVALID_INPUT")
        # A malformed target is reported before an unknown link endpoint.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"][0]["right"]["sample_id"] = "ghost"
        bad["targets"] = [{"dataset_id": "g", "field_id": "g"}]
        self._assert_error(run_json(bad), "INVALID_INPUT")
        # Unknown references beat unknown targets.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        bad["targets"] = [
            {"dataset_id": "g", "field_id": "g", "sample_id": "g"}
        ]
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")


if __name__ == "__main__":
    unittest.main()
