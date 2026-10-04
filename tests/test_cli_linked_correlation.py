"""End-to-end tests for the ``dq correlate-links`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "correlate-links"]


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


def field_ref(dataset, field):
    return {"dataset": dataset, "field": field}


def sample_ref(dataset, sample):
    return {"dataset_id": dataset, "sample_id": sample}


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": field_ref(src_dataset, src_field),
        "target": field_ref(dst_dataset, dst_field),
        "type": edge_type,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample_ref(left_dataset, left_sample),
        "right": sample_ref(right_dataset, right_sample),
    }


BASE_PAYLOAD = {
    "results": [
        result("r2", "dwd", "label", "样本-1", value="bad"),
        result("r1", "ods", "name", "样本-a", value="x"),
        result("r3", "dwd", "id", "样本-2", value=0),
    ],
    "lineage": {
        "datasets": ["ods", "dwd"],
        "fields": {"ods": ["name"], "dwd": ["id", "label"]},
        "edges": [edge("dwd", "label", "ods", "name", "upstream")],
    },
    "sample_links": [
        link("ods", "样本-a", "dwd", "样本-1"),
    ],
}


class CliCorrelateLinksSuccessTest(unittest.TestCase):
    def test_linked_events_grouped_and_sorted(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "events": [
                    {
                        "sample_refs": [
                            sample_ref("dwd", "样本-1"),
                            sample_ref("ods", "样本-a"),
                        ],
                        "rules": ["r1", "r2"],
                        "fields": [
                            field_ref("dwd", "label"),
                            field_ref("ods", "name"),
                        ],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    },
                    {
                        "sample_refs": [sample_ref("dwd", "样本-2")],
                        "rules": ["r3"],
                        "fields": [field_ref("dwd", "id")],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    },
                ]
            },
        )

    def test_empty_links_and_results(self):
        payload = {
            "results": [],
            "lineage": BASE_PAYLOAD["lineage"],
            "sample_links": [],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")), {"events": []}
        )

    def test_all_passing_outputs_empty_events(self):
        payload = json.loads(json.dumps(BASE_PAYLOAD))
        for item in payload["results"]:
            item["violated"] = False
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")), {"events": []}
        )


class CliCorrelateLinksErrorTest(unittest.TestCase):
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
        self._assert_error(run_json([1, 2]), "INVALID_LINKED_CORRELATION_INPUT")
        self._assert_error(run_json("links"), "INVALID_LINKED_CORRELATION_INPUT")

    def test_results_and_lineage_codes_still_apply(self):
        # results/lineage keep their correlate semantics and error code.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["results"]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["lineage"]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        # sample_links itself is required: missing it is a structural
        # error surfaced as INVALID_LINKED_CORRELATION_INPUT (None is not
        # a list).
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["sample_links"]
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["rule_id"] = ""
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

    def test_invalid_linked_correlation_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = "nope"
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["sample_links"][0]["right"]
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"][0]["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"][0]["left"]["dataset_id"] = ""
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [
            link("dwd", "样本-1", "dwd", "样本-1"),
        ]
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        first = link("ods", "样本-a", "dwd", "样本-1")
        bad["sample_links"] = [
            first,
            link("dwd", "样本-1", "ods", "样本-a"),
        ]
        self._assert_error(run_json(bad), "INVALID_LINKED_CORRELATION_INPUT")

    def test_unknown_correlation_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_CORRELATION_REFERENCE")

    def test_unknown_linked_correlation_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [link("ghost", "样本-1", "ods", "样本-a")]
        self._assert_error(
            run_json(bad), "UNKNOWN_LINKED_CORRELATION_REFERENCE"
        )

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [link("dwd", "不存在", "ods", "样本-a")]
        self._assert_error(
            run_json(bad), "UNKNOWN_LINKED_CORRELATION_REFERENCE"
        )

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed results beat malformed/unknown sample links.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["rule_id"] = ""
        bad["sample_links"] = [link("ghost", "x", "ghost", "x")]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")
        # Malformed links beat unknown link endpoints.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [
            {"left": sample_ref("ghost", "x"),
             "right": sample_ref("ghost", "x")}
        ]
        self._assert_error(
            run_json(bad), "INVALID_LINKED_CORRELATION_INPUT"
        )


if __name__ == "__main__":
    unittest.main()
