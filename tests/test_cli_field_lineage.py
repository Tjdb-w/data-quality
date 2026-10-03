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


def fld(table, column):
    return {"table": table, "column": column}


class CliFieldLineageSuccessTest(unittest.TestCase):
    PAYLOAD = {
        "fields": {
            "raw": ["id", "name"],
            "ods": ["id", "name"],
            "dwd": ["id", "name"],
            "ads": ["id"],
        },
        "edges": [
            {"source": fld("raw", "id"), "target": fld("ods", "id")},
            {"source": fld("raw", "name"), "target": fld("ods", "name")},
            {"source": fld("ods", "id"), "target": fld("dwd", "id")},
            {"source": fld("ods", "name"), "target": fld("dwd", "name")},
            {"source": fld("dwd", "id"), "target": fld("ads", "id")},
        ],
        "target": fld("dwd", "id"),
    }

    def test_optional_fields_default_to_both_unlimited(self):
        proc = run_json(self.PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], fld("dwd", "id"))
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])
        self.assertEqual(
            [(x["table"], x["column"]) for x in result["upstream"]["fields"]],
            [("dwd", "id"), ("ods", "id"), ("raw", "id")],
        )
        self.assertEqual(
            [(x["table"], x["column"]) for x in result["downstream"]["fields"]],
            [("dwd", "id"), ("ads", "id")],
        )
        self.assertEqual(
            [
                ((e["source"]["table"], e["source"]["column"]),
                 (e["target"]["table"], e["target"]["column"]))
                for e in result["upstream"]["edges"]
            ],
            [(("ods", "id"), ("dwd", "id")),
             (("raw", "id"), ("ods", "id"))],
        )
        self.assertEqual(
            [
                ((e["source"]["table"], e["source"]["column"]),
                 (e["target"]["table"], e["target"]["column"]))
                for e in result["downstream"]["edges"]
            ],
            [(("dwd", "id"), ("ads", "id"))],
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
            [{"table": "dwd", "column": "id", "depth": 0}],
        )
        self.assertEqual(result["downstream"], {"fields": [], "edges": []})


class CliFieldLineageErrorTest(unittest.TestCase):
    BASE = {
        "fields": {"raw": ["id"], "ods": ["id"]},
        "edges": [{"source": fld("raw", "id"), "target": fld("ods", "id")}],
        "target": fld("ods", "id"),
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
        proc = run_json({"edges": [], "target": fld("ods", "id")})
        self._assert_error(proc, "INVALID_FIELD_LINEAGE_INPUT")

        proc = run_json(
            {"fields": {}, "edges": [], "target": fld("ods", "id")}
        )
        self._assert_error(proc, "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["fields"] = {"raw": ["id", "id"], "ods": ["id"]}
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [
            {"source": fld("raw", "id"), "target": fld("ods", "id")},
            {"source": fld("raw", "id"), "target": fld("ods", "id")},
        ]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [{"source": fld("raw", "id"), "target": fld("ghost", "id")}]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [
            {"source": fld("raw", "id"), "target": fld("ods", "id"), "x": 1}
        ]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [
            {"source": {"table": "raw"}, "target": fld("ods", "id")}
        ]
        self._assert_error(run_json(bad), "INVALID_FIELD_LINEAGE_INPUT")

    def test_invalid_field_lineage_query_codes(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = 5
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
        bad["target"] = fld("ghost", "id")
        self._assert_error(run_json(bad), "UNKNOWN_FIELD_LINEAGE_TARGET")

        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = fld("ods", "ghost")
        self._assert_error(run_json(bad), "UNKNOWN_FIELD_LINEAGE_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Bad graph beats bad query and unknown target.
        payload = {
            "fields": {},
            "edges": [],
            "target": fld("ghost", "id"),
            "direction": "sideways",
            "max_depth": -1,
        }
        self._assert_error(run_json(payload), "INVALID_FIELD_LINEAGE_INPUT")
        # Bad query beats unknown target.
        payload = {
            "fields": {"a": ["x"]},
            "edges": [],
            "target": fld("ghost", "id"),
            "direction": "sideways",
        }
        self._assert_error(run_json(payload), "INVALID_FIELD_LINEAGE_QUERY")


if __name__ == "__main__":
    unittest.main()
