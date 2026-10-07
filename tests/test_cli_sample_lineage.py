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


def sample(dataset, sample_id):
    return {"dataset_id": dataset, "sample_id": sample_id}


def field(table, column):
    return {"table": table, "column": column}


def edge(src_dataset, src_sample, dst_dataset, dst_sample, witnesses):
    return {
        "source": sample(src_dataset, src_sample),
        "target": sample(dst_dataset, dst_sample),
        "witnesses": witnesses,
    }


def link(left_dataset, left_sample, right_dataset, right_sample):
    return {
        "left": sample(left_dataset, left_sample),
        "right": sample(right_dataset, right_sample),
    }


BASE_PAYLOAD = {
    "samples": [
        sample("raw", "r1"),
        sample("ods", "o1"),
        sample("dwd", "d1"),
        sample("ads", "a1"),
    ],
    "fields": {
        "raw": ["id", "name"],
        "ods": ["name"],
        "dwd": ["label"],
        "ads": ["label"],
    },
    "edges": [
        edge("raw", "r1", "ods", "o1",
             [field("raw", "name"), field("ods", "name")]),
        edge("ods", "o1", "dwd", "d1",
             [field("ods", "name"), field("dwd", "label")]),
        edge("dwd", "d1", "ads", "a1",
             [field("ads", "label"), field("dwd", "label")]),
    ],
    "sample_links": [
        link("raw", "r1", "ads", "a1"),
    ],
    "target": sample("dwd", "d1"),
}


class CliSampleLineageSuccessTest(unittest.TestCase):
    def test_defaults_to_both_unlimited(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], sample("dwd", "d1"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        self.assertEqual(
            [(s["dataset_id"], s["sample_id"], s["depth"])
             for s in result["upstream"]["samples"]],
            [("dwd", "d1", 0), ("ods", "o1", 1),
             ("raw", "r1", 2), ("ads", "a1", 3)],
        )
        # Edges sort by source sample ref. The link pulled ads.a1 into
        # the upstream side, so the last field edge is reported there as
        # well and sorts first (source dwd < ods < raw).
        self.assertEqual(
            [(e["source"]["sample_id"], e["target"]["sample_id"])
             for e in result["upstream"]["edges"]],
            [("d1", "a1"), ("o1", "d1"), ("r1", "o1")],
        )
        # Witnesses are sorted by (table, column), regardless of input.
        self.assertEqual(
            result["upstream"]["edges"][0],
            edge("dwd", "d1", "ads", "a1",
                 [field("ads", "label"), field("dwd", "label")]),
        )
        self.assertEqual(
            result["upstream"]["edges"][2],
            edge("raw", "r1", "ods", "o1",
                 [field("ods", "name"), field("raw", "name")]),
        )

        self.assertEqual(
            [(s["dataset_id"], s["sample_id"], s["depth"])
             for s in result["downstream"]["samples"]],
            [("dwd", "d1", 0), ("ads", "a1", 1),
             ("raw", "r1", 2), ("ods", "o1", 3)],
        )

    def test_explicit_direction_and_max_depth(self):
        payload = dict(BASE_PAYLOAD, direction="upstream", max_depth=1)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["max_depth"], 1)
        self.assertEqual(
            [(s["sample_id"], s["depth"])
             for s in result["upstream"]["samples"]],
            [("d1", 0), ("o1", 1)],
        )
        self.assertEqual(
            [(e["source"]["sample_id"], e["target"]["sample_id"])
             for e in result["upstream"]["edges"]],
            [("o1", "d1")],
        )
        self.assertEqual(result["downstream"], {"samples": [], "edges": []})

    def test_max_depth_zero_is_target_only(self):
        payload = dict(BASE_PAYLOAD, max_depth=0)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        for side in ("upstream", "downstream"):
            self.assertEqual(
                result[side]["samples"],
                [{"dataset_id": "dwd", "sample_id": "d1", "depth": 0}],
            )
            self.assertEqual(result[side]["edges"], [])

    def test_empty_links_and_isolated_target(self):
        payload = dict(BASE_PAYLOAD, sample_links=[])
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            [s["sample_id"] for s in result["upstream"]["samples"]],
            ["d1", "o1", "r1"],
        )
        self.assertEqual(
            [s["sample_id"] for s in result["downstream"]["samples"]],
            ["d1", "a1"],
        )

    def test_single_line_of_json_on_stdout(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.decode("utf-8")
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.endswith("\n"))
        json.loads(text)

    def test_utf8_output(self):
        payload = {
            "samples": [sample("数仓", "样本-1"), sample("数仓", "样本-2")],
            "fields": {"表": ["字段"]},
            "edges": [
                edge("数仓", "样本-1", "数仓", "样本-2",
                     [field("表", "字段")])
            ],
            "sample_links": [],
            "target": sample("数仓", "样本-1"),
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], sample("数仓", "样本-1"))
        self.assertEqual(
            result["downstream"]["samples"][1],
            {"dataset_id": "数仓", "sample_id": "样本-2", "depth": 1},
        )


class CliSampleLineageErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"samples": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_SAMPLE_LINEAGE_INPUT")
        self._assert_error(run_json("samples"), "INVALID_SAMPLE_LINEAGE_INPUT")

    def test_invalid_sample_lineage_input_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["samples"]
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["samples"].append(sample("raw", "r1"))  # duplicate sample
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["fields"]["raw"].append("id")  # duplicate field
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["edges"][0]["source"] = sample("ghost", "r1")
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["edges"][0]["witnesses"] = [field("ghost", "name")]
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["edges"].append(dict(bad["edges"][0]))  # exact duplicate
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = None
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [link("raw", "r1", "raw", "r1")]  # self link
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_INPUT")

    def test_invalid_sample_lineage_query_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["target"] = "dwd/d1"
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["target"] = {"dataset_id": "dwd"}
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["max_depth"] = -1
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["max_depth"] = True
        self._assert_error(run_json(bad), "INVALID_SAMPLE_LINEAGE_QUERY")

    def test_unknown_sample_lineage_target(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["target"] = sample("ghost", "d1")
        self._assert_error(run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_TARGET")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["target"] = sample("dwd", "ghost")
        self._assert_error(run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_TARGET")

    def test_unknown_sample_lineage_reference(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [link("ghost", "x", "ods", "o1")]
        self._assert_error(
            run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_REFERENCE"
        )

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["sample_links"] = [link("raw", "r1", "数仓", "不存在")]
        self._assert_error(
            run_json(bad), "UNKNOWN_SAMPLE_LINEAGE_REFERENCE"
        )

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Bad graph beats bad query, unknown target and unknown reference.
        payload = {
            "samples": [sample("raw", "r1"), sample("raw", "r1")],
            "fields": {"raw": ["id"]},
            "edges": [],
            "sample_links": [link("ghost", "x", "ghost", "y")],
            "target": "nope",
            "direction": "sideways",
            "max_depth": -1,
        }
        self._assert_error(
            run_json(payload), "INVALID_SAMPLE_LINEAGE_INPUT"
        )
        # Bad query beats unknown target and unknown reference.
        payload = {
            "samples": BASE_PAYLOAD["samples"],
            "fields": BASE_PAYLOAD["fields"],
            "edges": BASE_PAYLOAD["edges"],
            "sample_links": [link("ghost", "x", "ods", "o1")],
            "target": sample("ghost", "d1"),
            "direction": "sideways",
        }
        self._assert_error(
            run_json(payload), "INVALID_SAMPLE_LINEAGE_QUERY"
        )
        # Unknown target beats unknown link reference.
        payload = dict(payload, direction="both")
        self._assert_error(
            run_json(payload), "UNKNOWN_SAMPLE_LINEAGE_TARGET"
        )


if __name__ == "__main__":
    unittest.main()
