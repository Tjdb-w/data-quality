"""End-to-end tests for the ``dq sample-lineage`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "sample-lineage"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def sample(dataset, key):
    return {"dataset_id": dataset, "sample_id": key}


def field(table, column):
    return {"table": table, "column": column}


def witness(dataset, key, table, column):
    return {
        "dataset_id": dataset,
        "sample_id": key,
        "table": table,
        "column": column,
    }


def edge(source, target, witnesses):
    return {"source": source, "target": target, "witnesses": witnesses}


def link(left, right):
    return {"left": left, "right": right}


PAYLOAD = {
    "samples": [
        sample("ods", "a"),
        sample("dwd", "b"),
        sample("ads", "c"),
        sample("ads", "z"),
    ],
    "fields": {
        "ods": ["name"],
        "dwd": ["label"],
        "ads": ["label"],
    },
    "edges": [
        edge(
            field("ods", "name"),
            field("dwd", "label"),
            [
                witness("ods", "a", "ods", "name"),
                witness("dwd", "b", "dwd", "label"),
            ],
        ),
        edge(
            field("dwd", "label"),
            field("ads", "label"),
            [
                witness("dwd", "b", "dwd", "label"),
                witness("ads", "c", "ads", "label"),
            ],
        ),
    ],
    "sample_links": [
        link(sample("ads", "c"), sample("ads", "z")),
    ],
    "target": sample("dwd", "b"),
}


class CliSampleLineageSuccessTest(unittest.TestCase):
    def test_defaults_both_unlimited(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], sample("dwd", "b"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])
        self.assertEqual(
            [(s["dataset_id"], s["sample_id"], s["depth"])
             for s in result["upstream"]["samples"]],
            [("dwd", "b", 0), ("ods", "a", 1)],
        )
        self.assertEqual(
            [(s["dataset_id"], s["sample_id"], s["depth"])
             for s in result["downstream"]["samples"]],
            [("dwd", "b", 0), ("ads", "c", 1), ("ads", "z", 2)],
        )
        self.assertEqual(
            [(w["dataset_id"], w["sample_id"], w["table"], w["column"])
             for w in result["downstream"]["edges"][0]["witnesses"]],
            [("ads", "c", "ads", "label"),
             ("dwd", "b", "dwd", "label")],
        )

    def test_empty_relations_isolated_target(self):
        payload = {
            "samples": [sample("dwd", "b")],
            "fields": {"dwd": ["label"]},
            "edges": [],
            "sample_links": [],
            "target": sample("dwd", "b"),
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            result["upstream"],
            {"samples": [{**sample("dwd", "b"), "depth": 0}], "edges": []},
        )
        self.assertEqual(
            result["downstream"],
            {"samples": [{**sample("dwd", "b"), "depth": 0}], "edges": []},
        )

    def test_explicit_direction_and_depth(self):
        payload = dict(PAYLOAD, direction="upstream", max_depth=0)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(
            result["upstream"]["samples"],
            [{**sample("dwd", "b"), "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"],
                         {"samples": [], "edges": []})

    def test_utf8_output(self):
        payload = {
            "samples": [sample("表", "样本-1")],
            "fields": {"表": ["字段"]},
            "edges": [],
            "sample_links": [],
            "target": sample("表", "样本-1"),
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], sample("表", "样本-1"))

    def test_output_is_single_line(self):
        proc = run_json(PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.decode("utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)


class CliSampleLineageErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(set(body["error"]), {"code", "message"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"samples": [\xff]}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_SAMPLE_LINEAGE_INPUT")
        self._assert_error(run_json("samples"), "INVALID_SAMPLE_LINEAGE_INPUT")

    def test_invalid_input_codes(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["samples"] = "nope"
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["samples"].append(sample("dwd", "b"))
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["fields"] = {"ods": ["name", "name"]}
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["edges"] = [
            edge(
                field("ods", "name"),
                field("dwd", "label"),
                [witness("ods", "a", "ods", "ghost"),
                 witness("dwd", "b", "dwd", "label")],
            )
        ]
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["edges"][0]["witnesses"].append(
            witness("ods", "a", "ods", "name")
        )
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["sample_links"] = [
            link(sample("ads", "c"), sample("ads", "c"))
        ]
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

    def test_invalid_query_codes(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["target"] = "dwd/b"
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["target"] = {"dataset_id": "dwd"}
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["max_depth"] = -1
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(PAYLOAD))
        bad["max_depth"] = True
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

    def test_unknown_target(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["target"] = sample("dwd", "ghost")
        self._assert_error(run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_TARGET")

    def test_unknown_link_endpoint(self):
        bad = json.loads(json.dumps(PAYLOAD))
        bad["sample_links"] = [
            link(sample("ads", "c"), sample("ads", "ghost"))
        ]
        self._assert_error(run_json(bad),
                           "UNKNOWN_SAMPLE_LINEAGE_REFERENCE")

    def test_error_precedence(self):
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Bad structure beats bad query / unknown references.
        bad = {
            "samples": "nope",
            "fields": {},
            "edges": [],
            "sample_links": [
                link(sample("ads", "c"), sample("ads", "ghost"))
            ],
            "target": sample("dwd", "ghost"),
            "direction": "sideways",
            "max_depth": -1,
        }
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        # Malformed link beats an unknown link endpoint.
        bad = json.loads(json.dumps(PAYLOAD))
        bad["sample_links"] = [
            link(sample("ads", "ghost"), sample("ads", "ghost"))
        ]
        bad["target"] = sample("dwd", "ghost")
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        # Bad query beats unknown target / unknown endpoint.
        bad = json.loads(json.dumps(PAYLOAD))
        bad["direction"] = "sideways"
        bad["target"] = sample("dwd", "ghost")
        bad["sample_links"] = [
            link(sample("ads", "c"), sample("ads", "ghost"))
        ]
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        # Unknown target beats unknown link endpoint.
        bad = json.loads(json.dumps(PAYLOAD))
        bad["target"] = sample("dwd", "ghost")
        bad["sample_links"] = [
            link(sample("ads", "c"), sample("ads", "ghost"))
        ]
        self._assert_error(run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_TARGET")


if __name__ == "__main__":
    unittest.main()
