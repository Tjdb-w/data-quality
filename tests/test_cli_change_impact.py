"""End-to-end tests for the ``dq change-impact`` command line interface."""

import copy
import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "change-impact"]


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


def col(sd, sf, td, tf):
    return {
        "sourceDataset": sd,
        "sourceField": sf,
        "targetDataset": td,
        "targetField": tf,
    }


def rel(sd, td):
    return {"sourceDataset": sd, "targetDataset": td}


BASE = {
    "metadata": {
        "datasets": {
            "ods": [{"field": "name", "type": "string"}, "id"],
            "dwd": [{"field": "label", "type": "string"}],
            "ads": [{"field": "label", "type": "int64"}],
            "rpt": ["n"],
        },
        "lineageEdges": [
            col("ods", "name", "dwd", "label"),
            col("dwd", "label", "ads", "label"),
            rel("ads", "rpt"),
        ],
    },
    "change": {
        "dataset": "ods",
        "changeType": "type_change",
        "fields": ["name"],
    },
}


class CliChangeImpactSuccessTest(unittest.TestCase):
    def test_full_report_matches_python_entry_point(self):
        from data_quality import analyze_change_impact

        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, analyze_change_impact(copy.deepcopy(BASE)))
        self.assertEqual(proc.stderr, b"")

    def test_report_shape_and_order(self):
        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(
            body["change"],
            {"dataset": "ods", "changeType": "type_change",
             "fields": ["name"]},
        )
        rows = body["affectedDatasets"]
        # dwd.label has the same raw string type: filtered out. ads.label
        # differs (known), rpt is downstream only at dataset level.
        self.assertEqual([r["dataset"] for r in rows], ["ads", "rpt"])
        ads, rpt = rows
        self.assertEqual(ads["distance"], 2)
        self.assertEqual(ads["path"], ["ods", "dwd", "ads"])
        self.assertEqual(ads["fieldImpact"], "known")
        self.assertEqual(
            ads["affectedFields"][0]["sources"], [ref("ods", "name")]
        )
        self.assertEqual(rpt["distance"], 3)
        self.assertEqual(rpt["path"], ["ods", "dwd", "ads", "rpt"])
        self.assertEqual(rpt["fieldImpact"], "unknown")
        self.assertEqual(rpt["affectedFields"], [])

    def test_empty_downstream_is_ok(self):
        payload = copy.deepcopy(BASE)
        payload["change"] = {
            "dataset": "rpt", "changeType": "delete", "fields": ["n"],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["affectedDatasets"], [])
        self.assertEqual(body["change"]["changeType"], "delete")

    def test_rename_single_summary_with_both_sources(self):
        payload = {
            "metadata": {
                "datasets": {"a": ["old", "new"], "b": ["z"]},
                "lineageEdges": [
                    col("a", "old", "b", "z"),
                    col("a", "new", "b", "z"),
                ],
            },
            "change": {
                "dataset": "a", "changeType": "rename",
                "oldField": "old", "newField": "new",
            },
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body["change"]),
                         {"dataset", "changeType", "oldField", "newField"})
        hit = body["affectedDatasets"][0]["affectedFields"][0]
        self.assertEqual(
            hit["sources"], [ref("a", "new"), ref("a", "old")]
        )

    def test_nested_edge_shape(self):
        payload = {
            "metadata": {
                "datasets": {"a": ["x"], "b": ["y"]},
                "lineageEdges": [
                    {"source": {"dataset": "a", "field": "x"},
                     "target": {"dataset": "b", "field": "y"}}
                ],
            },
            "change": {"dataset": "a", "changeType": "delete",
                       "fields": ["x"]},
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["affectedDatasets"][0]["fieldImpact"], "known")

    def test_output_is_one_utf8_json_line(self):
        payload = copy.deepcopy(BASE)
        payload["metadata"]["datasets"]["ods"].append(
            {"field": "字段-α", "type": "字符串"}
        )
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.decode("utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        json.loads(text)

    def test_repeated_calls_identical(self):
        first = run_json(BASE)
        second = run_json(copy.deepcopy(BASE))
        self.assertEqual(first.stdout, second.stdout)


class CliChangeImpactErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(
            run_cli('{"metadata": \xff}'.encode("utf-8")), "INVALID_JSON"
        )

    def test_structural_errors(self):
        self._assert_error(run_json(None), "INVALID_CHANGE_IMPACT_INPUT")
        self._assert_error(run_json([1]), "INVALID_CHANGE_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        del bad["change"]
        self._assert_error(run_json(bad), "INVALID_CHANGE_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["extra"] = 1
        self._assert_error(run_json(bad), "INVALID_CHANGE_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["metadata"]["datasets"]["ods"].append("")
        self._assert_error(run_json(bad), "INVALID_CHANGE_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["metadata"]["lineageEdges"].append(rel("ghost", "ads"))
        self._assert_error(run_json(bad), "INVALID_CHANGE_IMPACT_INPUT")

        bad = copy.deepcopy(BASE)
        bad["change"] = {"dataset": "ods", "changeType": "drop",
                         "fields": ["name"]}
        self._assert_error(run_json(bad), "INVALID_CHANGE_IMPACT_INPUT")

    def test_business_errors(self):
        bad = copy.deepcopy(BASE)
        bad["change"]["dataset"] = "ghost"
        self._assert_error(run_json(bad), "DATASET_NOT_FOUND")

        bad = copy.deepcopy(BASE)
        bad["change"]["fields"] = ["ghost"]
        self._assert_error(run_json(bad), "FIELD_NOT_FOUND")

        bad = copy.deepcopy(BASE)
        bad["change"] = {"dataset": "ods", "changeType": "rename",
                         "oldField": "id", "newField": "id"}
        self._assert_error(run_json(bad), "INVALID_RENAME")

        bad = copy.deepcopy(BASE)
        bad["change"]["fields"] = ["id", "id"]
        self._assert_error(run_json(bad), "DUPLICATE_FIELD")

        bad = copy.deepcopy(BASE)
        bad["change"]["fields"] = []
        self._assert_error(run_json(bad), "INVALID_FIELDS")

    def test_no_partial_result(self):
        bad = copy.deepcopy(BASE)
        bad["change"]["fields"] = ["ghost"]
        proc = run_json(bad)
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("affectedDatasets", body)
        self.assertNotIn("status", body)


if __name__ == "__main__":
    unittest.main()
