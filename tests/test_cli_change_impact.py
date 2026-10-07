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


def fref(dataset, field):
    return {"dataset": dataset, "field": field}


def dref(dataset):
    return {"dataset": dataset}


def cedge(sd, sf, td, tf):
    return {"source": fref(sd, sf), "target": fref(td, tf)}


def dedge(sd, td):
    return {"source": dref(sd), "target": dref(td)}


def decl(names, type_="string"):
    return [{"name": name, "type": type_} for name in names]


METADATA = {
    "datasets": {
        "ods": decl(["name", "full_name"]),
        "dwd": decl(["label"]),
        "ads": decl(["out"]),
        "rpt": decl(["x"]),
    },
    "edges": [
        cedge("ods", "name", "dwd", "label"),
        cedge("ods", "full_name", "dwd", "label"),
        cedge("dwd", "label", "ads", "out"),
        dedge("ads", "rpt"),
    ],
}

BASE = {
    "dataset": "ods",
    "changeType": "delete",
    "fields": ["name"],
    "metadata": METADATA,
}


class CliChangeImpactSuccessTest(unittest.TestCase):
    def test_basic_delete(self):
        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "status": "ok",
                "change": {
                    "dataset": "ods",
                    "changeType": "delete",
                    "fields": ["name"],
                },
                "affectedDatasets": [
                    {
                        "dataset": "dwd",
                        "distance": 1,
                        "unknown": False,
                        "fields": [
                            {
                                "field": "label",
                                "sources": [
                                    {
                                        "dataset": "ods",
                                        "field": "name",
                                        "path": [
                                            fref("ods", "name"),
                                            fref("dwd", "label"),
                                        ],
                                    }
                                ],
                            }
                        ],
                        "path": [dref("ods"), dref("dwd")],
                    },
                    {
                        "dataset": "ads",
                        "distance": 2,
                        "unknown": False,
                        "fields": [
                            {
                                "field": "out",
                                "sources": [
                                    {
                                        "dataset": "ods",
                                        "field": "name",
                                        "path": [
                                            fref("ods", "name"),
                                            fref("dwd", "label"),
                                            fref("ads", "out"),
                                        ],
                                    }
                                ],
                            }
                        ],
                        "path": [dref("ods"), dref("dwd"), dref("ads")],
                    },
                    {
                        "dataset": "rpt",
                        "distance": 3,
                        "unknown": True,
                        "fields": [],
                        "path": [
                            dref("ods"),
                            dref("dwd"),
                            dref("ads"),
                            dref("rpt"),
                        ],
                    },
                ],
            },
        )

    def test_result_matches_python_entry_point(self):
        from data_quality import analyze_change_impact

        proc = run_json(BASE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body, analyze_change_impact(copy.deepcopy(BASE)))

    def test_rename_payload(self):
        payload = {
            "dataset": "ods",
            "changeType": "rename",
            "fields": {"oldField": "name", "newField": "full_name"},
            "metadata": METADATA,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body["change"],
            {
                "dataset": "ods",
                "changeType": "rename",
                "oldField": "name",
                "newField": "full_name",
            },
        )
        dwd = next(
            d for d in body["affectedDatasets"] if d["dataset"] == "dwd"
        )
        self.assertEqual(
            [s["field"] for s in dwd["fields"][0]["sources"]],
            ["full_name", "name"],
        )

    def test_type_change_payload(self):
        payload = {
            "dataset": "ods",
            "changeType": "type_change",
            "fields": ["name"],
            "newType": "varchar",
            "metadata": METADATA,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["change"]["newType"], "varchar")
        self.assertEqual(body["change"]["fields"], ["name"])

    def test_no_downstream_is_still_ok(self):
        metadata = {"datasets": {"a": decl(["x"])}, "edges": []}
        payload = {
            "dataset": "a",
            "changeType": "delete",
            "fields": ["x"],
            "metadata": metadata,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["affectedDatasets"], [])
        self.assertEqual(body["change"]["dataset"], "a")

    def test_utf8_round_trip(self):
        metadata = {
            "datasets": {
                "数据集": decl(["名称"]),
                "报告": decl(["标签"]),
            },
            "edges": [cedge("数据集", "名称", "报告", "标签")],
        }
        payload = {
            "dataset": "数据集",
            "changeType": "delete",
            "fields": ["名称"],
            "metadata": metadata,
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body["affectedDatasets"][0]["dataset"], "报告"
        )
        self.assertIn("数据集".encode("utf-8"), proc.stdout)


class CliChangeImpactErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"dataset": \xff}'), "INVALID_JSON")

    def test_business_error_codes(self):
        cases = [
            ({**BASE, "dataset": "ghost"}, "DATASET_NOT_FOUND"),
            ({**BASE, "fields": ["ghost"]}, "FIELD_NOT_FOUND"),
            ({**BASE, "fields": ["name", "name"]}, "DUPLICATE_FIELD"),
            ({**BASE, "fields": []}, "INVALID_FIELDS"),
            (
                {
                    "dataset": "ods",
                    "changeType": "rename",
                    "fields": {"oldField": "n", "newField": "n"},
                    "metadata": METADATA,
                },
                "INVALID_RENAME",
            ),
            ({**BASE, "changeType": "drop"}, "INVALID_CHANGE_IMPACT_INPUT"),
            ([1, 2], "INVALID_CHANGE_IMPACT_INPUT"),
            ("ods", "INVALID_CHANGE_IMPACT_INPUT"),
        ]
        for payload, code in cases:
            self._assert_error(run_json(payload), code)

    def test_missing_metadata(self):
        payload = {k: v for k, v in BASE.items() if k != "metadata"}
        self._assert_error(run_json(payload), "INVALID_CHANGE_IMPACT_INPUT")

    def test_extra_top_level_key(self):
        payload = {**BASE, "unexpected": 1}
        self._assert_error(run_json(payload), "INVALID_CHANGE_IMPACT_INPUT")

    def test_metadata_edge_undeclared(self):
        payload = copy.deepcopy(BASE)
        payload["metadata"]["edges"].append(
            cedge("ods", "name", "nope", "label")
        )
        self._assert_error(run_json(payload), "INVALID_CHANGE_IMPACT_INPUT")

    def test_no_partial_result(self):
        proc = run_json({**BASE, "fields": ["ghost"]})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertNotIn("affectedDatasets", body)
        self.assertNotIn("change", body)


if __name__ == "__main__":
    unittest.main()
