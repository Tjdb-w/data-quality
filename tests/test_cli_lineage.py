"""End-to-end tests for the ``dq lineage`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "lineage"]


def run_cli(payload_bytes):
    return subprocess.run(
        PKG,
        input=payload_bytes,
        capture_output=True,
    )


def run_json(obj):
    return run_cli(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class CliLineageSuccessTest(unittest.TestCase):
    PAYLOAD = {
        "nodes": ["raw", "ods", "dwd", "ads"],
        "edges": [
            {"source": "raw", "target": "ods"},
            {"source": "ods", "target": "dwd"},
            {"source": "dwd", "target": "ads"},
        ],
        "target": "dwd",
    }

    def test_optional_fields_default_to_both_unlimited(self):
        proc = run_json(self.PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["target"], "dwd")
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])
        self.assertEqual(
            [n["id"] for n in result["upstream"]["nodes"]],
            ["dwd", "ods", "raw"],
        )
        self.assertEqual(
            [n["id"] for n in result["downstream"]["nodes"]],
            ["dwd", "ads"],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in result["upstream"]["edges"]],
            [("ods", "dwd"), ("raw", "ods")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in result["downstream"]["edges"]],
            [("dwd", "ads")],
        )

    def test_explicit_direction_and_max_depth_echoed(self):
        payload = dict(self.PAYLOAD, direction="upstream", max_depth=0)
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(result["max_depth"], 0)
        self.assertEqual(result["upstream"]["nodes"], [{"id": "dwd", "depth": 0}])
        self.assertEqual(result["downstream"], {"nodes": [], "edges": []})


class CliLineageErrorTest(unittest.TestCase):
    BASE = {
        "nodes": ["raw", "ods"],
        "edges": [{"source": "raw", "target": "ods"}],
        "target": "ods",
    }

    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"nodes": [\xff]}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_LINEAGE_INPUT")
        self._assert_error(run_json("nodes"), "INVALID_LINEAGE_INPUT")

    def test_invalid_lineage_input_codes(self):
        proc = run_json({"edges": [], "target": "ods"})
        self._assert_error(proc, "INVALID_LINEAGE_INPUT")

        proc = run_json({"nodes": [], "edges": [], "target": "ods"})
        self._assert_error(proc, "INVALID_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["nodes"] = ["raw", "raw"]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [
            {"source": "raw", "target": "ods"},
            {"source": "raw", "target": "ods"},
        ]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [{"source": "raw", "target": "ghost"}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_INPUT")

        bad = json.loads(json.dumps(self.BASE))
        bad["edges"] = [{"source": "raw", "target": "ods", "x": 1}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_INPUT")

    def test_invalid_lineage_query_codes(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = 5
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["direction"] = "sideways"
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["max_depth"] = -1
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")

        bad = json.loads(json.dumps(self.BASE))
        bad["max_depth"] = True
        self._assert_error(run_json(bad), "INVALID_LINEAGE_QUERY")

    def test_unknown_lineage_target(self):
        bad = json.loads(json.dumps(self.BASE))
        bad["target"] = "ghost"
        self._assert_error(run_json(bad), "UNKNOWN_LINEAGE_TARGET")

    def test_error_precedence(self):
        # Bad JSON beats everything.
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Bad graph beats bad query and unknown target.
        payload = {
            "nodes": [],
            "edges": [],
            "target": "ghost",
            "direction": "sideways",
            "max_depth": -1,
        }
        self._assert_error(run_json(payload), "INVALID_LINEAGE_INPUT")
        # Bad query beats unknown target.
        payload = {
            "nodes": ["a"],
            "edges": [],
            "target": "ghost",
            "direction": "sideways",
        }
        self._assert_error(run_json(payload), "INVALID_LINEAGE_QUERY")


if __name__ == "__main__":
    unittest.main()
