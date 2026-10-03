"""End-to-end tests for the ``dq correlate`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "correlate"]


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


def edge(src_dataset, src_field, dst_dataset, dst_field, edge_type="upstream"):
    return {
        "source": endpoint(src_dataset, src_field),
        "target": endpoint(dst_dataset, dst_field),
        "type": edge_type,
    }


BASE_PAYLOAD = {
    "results": [
        result("r2", "dwd", "label", "样本-1", value="bad"),
        result("r1", "ods", "name", "样本-1", value="x"),
        result("r3", "dwd", "id", "样本-2", value=0),
    ],
    "lineage": {
        "datasets": ["ods", "dwd"],
        "fields": {"ods": ["name"], "dwd": ["id", "label"]},
        "edges": [edge("dwd", "label", "ods", "name", "upstream")],
    },
}


class CliCorrelateSuccessTest(unittest.TestCase):
    def test_events_grouped_and_sorted(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "events": [
                    {
                        "sample_id": "样本-1",
                        "rules": ["r1", "r2"],
                        "fields": [
                            endpoint("dwd", "label"),
                            endpoint("ods", "name"),
                        ],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    },
                    {
                        "sample_id": "样本-2",
                        "rules": ["r3"],
                        "fields": [endpoint("dwd", "id")],
                        "upstream_fields": [],
                        "downstream_fields": [],
                    },
                ]
            },
        )

    def test_empty_results(self):
        proc = run_json({"results": [], "lineage": BASE_PAYLOAD["lineage"]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, {"events": []})


class CliCorrelateErrorTest(unittest.TestCase):
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
        self._assert_error(run_json([1, 2]), "INVALID_CORRELATION_INPUT")
        self._assert_error(run_json("results"), "INVALID_CORRELATION_INPUT")

    def test_invalid_correlation_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["results"]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["lineage"]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["rule_id"] = ""
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"].append(result("r2", "dwd", "label", "样本-1", value="other"))
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["lineage"]["edges"] = [
            edge("dwd", "label", "ods", "name", "upstream"),
            edge("dwd", "label", "ods", "name", "downstream"),
        ]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["lineage"]["edges"] = [edge("dwd", "label", "ghost", "name")]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["lineage"]["edges"] = [edge("dwd", "label", "ods", "name", " sideways")]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")

    def test_unknown_correlation_reference_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_CORRELATION_REFERENCE")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["field_id"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_CORRELATION_REFERENCE")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Malformed input beats unknown references.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["results"][0]["dataset_id"] = "ghost"
        bad["lineage"]["edges"] = [edge("dwd", "label", "ods", "name", "bad")]
        self._assert_error(run_json(bad), "INVALID_CORRELATION_INPUT")


if __name__ == "__main__":
    unittest.main()
