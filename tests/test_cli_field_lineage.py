"""End-to-end tests for the ``dq field-lineage`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "field-lineage"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def field(table, column):
    return {"table": table, "column": column}


def edge(src_table, src_column, dst_table, dst_column):
    return {
        "source": field(src_table, src_column),
        "target": field(dst_table, dst_column),
    }


class CliFieldLineageSuccessTest(unittest.TestCase):
    PAYLOAD = {
        "fields": {
            "raw": ["id", "name"],
            "ods": ["id", "name"],
            "dwd": ["id", "label"],
            "ads": ["label"],
        },
        "edges": [
            edge("raw", "id", "ods", "id"),
            edge("raw", "name", "ods", "name"),
            edge("ods", "id", "dwd", "id"),
            edge("ods", "name", "dwd", "label"),
            edge("dwd", "label", "ads", "label"),
        ],
        "target": field("dwd", "label"),
    }

    def test_optional_fields_default_to_both_unlimited(self):
        proc = run_json(self.PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], field("dwd", "label"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])
        self.assertEqual(
            [(f["table"], f["column"], f["depth"])
             for f in result["upstream"]["fields"]],
            [("dwd", "label", 0), ("ods", "name", 1), ("raw", "name", 2)],
        )
        self.assertEqual(
            result["upstream"]["edges"],
            [edge("ods", "name", "dwd", "label"),
             edge("raw", "name", "ods", "name")],
        )
        self.assertEqual(
            [(f["table"], f["column"], f["depth"])
             for f in result["downstream"]["fields"]],
            [("dwd", "label", 0), ("ads", "label", 1)],
        )
        self.assertEqual(
            result["downstream"]["edges"],
            [edge("dwd", "label", "ads", "label")],
        )

    def test_explicit_direction_and_max_depth_echoed(self):
        payload = dict(self.PAYLOAD, direction="upstream", max_depth=0)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(
            result["upstream"]["fields"],
            [{"table": "dwd", "column": "label", "depth": 0}],
        )
        self.assertEqual(result["upstream"]["edges"], [])
        self.assertEqual(result["downstream"], {"fields": [], "edges": []})

    def test_utf8_output(self):
        payload = {
            "fields": {"表": ["字段"]},
            "edges": [],
            "target": {"table": "表", "column": "字段"},
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], {"table": "表", "column": "字段"})


class CliFieldLineageErrorTest(unittest.TestCase):
    BASE = {
        "fields": {"raw": ["id"], "ods": ["id"]},
        "edges": [edge("raw", "id", "ods", "id")],
        "target": field("ods", "id"),
    }

    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"fields": {\xff}}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_FIELD_LINEAGE_INPUT")
        self._assert_error(run_json("fields"), "INVALID_FIELD_LINEAGE_INPUT")

    def test_invalid_field_lineage_input_codes(self):
        proc = run_json({"edges": [], "target": field("ods", "id")})
        self._assert_error(proc, "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["fields"] = {"raw": ["id", "id"]}
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [
            edge("raw", "id", "ods", "id"),
            edge("raw", "id", "ods", "id"),
        ]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [edge("raw", "id", "ghost", "id")]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [dict(edge("raw", "id", "ods", "id"), x=1)]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [{"source": "raw.id", "target": field("ods", "id")}]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

    def test_invalid_field_lineage_query_codes(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = "ods.id"
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = {"table": "ods"}
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["max_depth"] = -1
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["max_depth"] = True
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_QUERY")

    def test_unknown_field_lineage_target(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = field("ghost", "id")
        self._assert_error(run_json(bad), "UNKNOWN_FIELD_LINEAGE_TARGET")

        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = field("raw", "ghost")
        self._assert_error(run_json(bad), "UNKNOWN_FIELD_LINEAGE_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Bad graph beats bad query and unknown target.
        payload = {
            "fields": {"t": ["a", "a"]},
            "edges": [],
            "target": "nope",
            "direction": "sideways",
            "max_depth": -1,
        }
        self._assert_error(run_json(payload), "INVALID_FIELD_LINEAGE_INPUT")
        # Bad query beats unknown target.
        payload = {
            "fields": {"t": ["a"]},
            "edges": [],
            "target": field("ghost", "a"),
            "direction": "sideways",
        }
        self._assert_error(run_json(payload), "INVALID_FIELD_LINEAGE_QUERY")


if __name__ == "__main__":
    unittest.main()
