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


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def path_node(dataset, field):
    return {"dataset_id": dataset, "field_id": field}


def sample_node(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_node(left_dataset, left_sample),
        "right": sample_node(right_dataset, right_sample),
    }


BASE_PAYLOAD = {
    "results": [
        result("r1", "ods", "name", "样本-o", value="x"),
        result("r2", "dwd", "label", "样本-s", value="bad"),
        result("r3", "ads", "label", "样本-a", value="z"),
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
        link("dwd", "样本-s", "ods", "样本-o"),
        link("dwd", "样本-s", "ads", "样本-a"),
    ],
    "targets": [
        {"dataset_id": "dwd", "field_id": "label", "sample_id": "样本-s"}
    ],
}


class CliLinkedOriginsSuccessTest(unittest.TestCase):
    def test_report_with_cross_sample_origin_and_impact(self):
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
                            "sample_id": "样本-s",
                        },
                        "originEvidence": [
                            {
                                "rule_id": "r2",
                                "dataset_id": "dwd",
                                "field_id": "label",
                                "sample_id": "样本-s",
                                "violated": True,
                                "value": "bad",
                                "sample_path": [
                                    sample_node("dwd", "样本-s"),
                                ],
                                "path": [path_node("dwd", "label")],
                            },
                            {
                                "rule_id": "r1",
                                "dataset_id": "ods",
                                "field_id": "name",
                                "sample_id": "样本-o",
                                "violated": True,
                                "value": "x",
                                "sample_path": [
                                    sample_node("dwd", "样本-s"),
                                    sample_node("ods", "样本-o"),
                                ],
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
                                "sample_id": "样本-a",
                                "violated": True,
                                "value": "z",
                                "sample_path": [
                                    sample_node("dwd", "样本-s"),
                                    sample_node("ads", "样本-a"),
                                ],
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
        self.assertIn("样本-s".encode("utf-8"), proc.stdout)

    def test_success_output_is_one_json_line_and_exit_zero(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.count(b"\n"), 1)
        self.assertTrue(proc.stdout.endswith(b"\n"))
        self.assertEqual(proc.stderr, b"")

    def test_unlinked_sample_yields_no_cross_sample_evidence(self):
        payload = json.loads(json.dumps(BASE_PAYLOAD))
        payload["sample_links"] = [
            link("dwd", "样本-s", "ods", "样本-o")
        ]
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout.decode("utf-8"))["reports"][0]
        self.assertEqual(
            [item["rule_id"] for item in report["originEvidence"]],
            ["r2", "r1"],
        )
        self.assertEqual(report["impactEvidence"], [])


class CliLinkedOriginsErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        self.assertEqual(proc.stdout.count(b"\n"), 1)

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"results": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_INPUT")
        self._assert_error(run_json("results"), "INVALID_INPUT")

    def test_top_level_keys_must_be_exactly_the_four(self):
        for key in ("results", "lineage", "sample_links", "targets"):
            bad = json.loads(json.dumps(BASE_PAYLOAD))
            del bad[key]
            self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_INPUT")

    def test_invalid_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"dataset_id": "dwd", "field_id": "label"}]
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"][0]["sample_id"] = ""
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["violated"] = "yes"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = "nope"
        self._assert_error(run_json(bad), "INVALID_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [
            link("dwd", "样本-s", "dwd", "样本-s")
        ]
        self._assert_error(run_json(bad), "INVALID_INPUT")

    def test_unknown_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [
            link("dwd", "样本-s", "ods", "ghost")
        ]
        self._assert_error(run_json(bad), "UNKNOWN_REFERENCE")

    def test_unknown_target_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [
            {"dataset_id": "dwd", "field_id": "label", "sample_id": "zzz"}
        ]
        self._assert_error(run_json(bad), "UNKNOWN_TARGET")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][1]["violated"] = False
        self._assert_error(run_json(bad), "UNKNOWN_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed input beats unknown references and targets.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"] = "nope"
        bad["targets"] = [
            {"dataset_id": "g", "field_id": "g", "sample_id": "g"}
        ]
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
