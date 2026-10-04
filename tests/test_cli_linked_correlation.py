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


def endpoint(dataset, field):
    return {"dataset": dataset, "field": field}


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": {"dataset_id": left_dataset, "sample_id": left_sample},
        "right": {"dataset_id": right_dataset, "sample_id": right_sample},
    }


BASE_PAYLOAD = {
    "results": [
        result("r2", "dwd", "label", "样本-1", value="bad"),
        result("r1", "ods", "name", "样本-2", value="x"),
        result("r3", "dwd", "id", "样本-3", violated=False),
    ],
    "lineage": {
        "datasets": ["ods", "dwd"],
        "fields": {"ods": ["name"], "dwd": ["id", "label"]},
        "edges": [
            {
                "source": endpoint("dwd", "label"),
                "target": endpoint("ods", "name"),
                "type": "upstream",
            }
        ],
    },
    "sample_links": [
        link("dwd", "样本-1", "ods", "样本-2"),
        link("ods", "样本-2", "dwd", "样本-3"),
    ],
}


class CliCorrelateLinksSuccessTest(unittest.TestCase):
    def test_events_grouped_across_linked_samples(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "events": [
                    {
                        "sample_refs": [
                            {"dataset_id": "dwd", "sample_id": "样本-1"},
                            {"dataset_id": "ods", "sample_id": "样本-2"},
                        ],
                        "rules": ["r1", "r2"],
                        "fields": [
                            endpoint("dwd", "label"),
                            endpoint("ods", "name"),
                        ],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    }
                ]
            },
        )

    def test_output_is_utf8(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("样本-1".encode("utf-8"), proc.stdout)


class CliCorrelateLinksErrorTest(unittest.TestCase):
    def assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)

    def test_invalid_json(self):
        proc = run_cli(b"{not json")
        self.assert_error(proc, "INVALID_JSON")

    def test_non_object_payload(self):
        proc = run_json([1, 2, 3])
        self.assert_error(proc, "INVALID_LINKED_CORRELATION_INPUT")

    def test_malformed_links(self):
        payload = dict(BASE_PAYLOAD)
        payload["sample_links"] = [link("dwd", "样本-1", "dwd", "样本-1")]
        self.assert_error(run_json(payload), "INVALID_LINKED_CORRELATION_INPUT")

        payload = dict(BASE_PAYLOAD)
        payload["sample_links"] = [{"left": {"dataset_id": "dwd"}}]
        self.assert_error(run_json(payload), "INVALID_LINKED_CORRELATION_INPUT")

    def test_malformed_results(self):
        payload = dict(BASE_PAYLOAD)
        payload["results"] = [{"rule_id": "r1"}]
        self.assert_error(run_json(payload), "INVALID_LINKED_CORRELATION_INPUT")

    def test_unknown_link_endpoint(self):
        payload = dict(BASE_PAYLOAD)
        payload["sample_links"] = [link("dwd", "样本-1", "ods", "样本-9")]
        self.assert_error(
            run_json(payload), "UNKNOWN_LINKED_CORRELATION_REFERENCE"
        )


if __name__ == "__main__":
    unittest.main()
