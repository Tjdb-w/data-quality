"""End-to-end tests for the ``dq lineage-diff`` command line interface."""

import json
import subprocess
import sys
import unittest


PKG = [sys.executable, "-m", "data_quality", "lineage-diff"]


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


def snapshot(fields, edges):
    return {"fields": fields, "edges": edges}


GRAPH = {
    "ods": ["a"],
    "mid": ["m"],
    "dwd": ["x", "y"],
    "ads": ["z"],
}

EDGES = [
    edge("ods", "a", "mid", "m"),
    edge("mid", "m", "dwd", "x"),
    edge("dwd", "x", "ads", "z"),
]

BASE_PAYLOAD = {
    "baseline": snapshot(GRAPH, EDGES),
    "current": snapshot(
        dict(GRAPH, rpt=["p"]),
        EDGES + [edge("ads", "z", "rpt", "p")],
    ),
    "targets": [field("dwd", "y"), field("ads", "z")],
}


class CliLineageDiffSuccessTest(unittest.TestCase):
    def test_diff_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(
            body["summary"],
            {
                "added_field_count": 1,
                "removed_field_count": 0,
                "added_edge_count": 1,
                "removed_edge_count": 0,
                "changed_target_count": 1,
                "unchanged_target_count": 1,
            },
        )
        self.assertEqual(
            body["changes"],
            [
                {"type": "field_added", "field": field("rpt", "p")},
                {"type": "edge_added", "edge": edge("ads", "z", "rpt", "p")},
            ],
        )
        self.assertEqual(
            [report["field"] for report in body["targets"]],
            [field("dwd", "y"), field("ads", "z")],
        )
        isolated, sink = body["targets"]
        self.assertEqual(isolated["status"], "unchanged")
        self.assertEqual(sink["status"], "changed")
        self.assertEqual(
            sink["downstream_delta"]["added_fields"], [field("rpt", "p")]
        )
        self.assertEqual(
            sink["downstream_delta"]["added_edges"],
            [edge("ads", "z", "rpt", "p")],
        )

    def test_added_and_removed_targets(self):
        payload = {
            "baseline": snapshot({"ods": ["a"], "ads": ["z"]}, []),
            "current": snapshot({"ods": ["a"], "dwd": ["x"]}, []),
            "targets": [field("ads", "z"), field("dwd", "x")],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            [report["status"] for report in body["targets"]],
            ["removed", "added"],
        )
        self.assertEqual(body["summary"]["changed_target_count"], 2)
        self.assertEqual(body["summary"]["unchanged_target_count"], 0)

    def test_single_line_of_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)

    def test_top_level_key_order(self):
        proc = run_json(BASE_PAYLOAD)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            list(body), ["status", "summary", "changes", "targets"]
        )

    def test_nothing_written_to_stderr_on_success(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")


class CliLineageDiffErrorTest(unittest.TestCase):
    def _assert_error(self, proc, code):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"baseline": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_LINEAGE_SNAPSHOT")
        self._assert_error(run_json("snapshots"), "INVALID_LINEAGE_SNAPSHOT")

    def test_invalid_snapshot_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["targets"]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["baseline"]["edges"]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["fields"]["ods"] = [1]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["edges"][0]["extra"] = True
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["unexpected"] = True
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

    def test_invalid_query_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = []
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [field("dwd", "y"), field("dwd", "y")]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"table": "dwd", "column": ""}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"table": "dwd"}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

    def test_unknown_target_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [field("ghost", "g")]
        self._assert_error(run_json(bad), "UNKNOWN_LINEAGE_DIFF_TARGET")

    def test_error_precedence(self):
        self._assert_error(run_cli(b"{"), "INVALID_JSON")
        # Snapshot structure errors precede query errors.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["fields"]["ods"] = [1]
        bad["targets"] = []
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")
        # Query-shape errors precede unknown target existence checks.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"table": "ghost", "column": "g", "extra": 1}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

    def test_nothing_written_to_stderr_on_error(self):
        proc = run_json([1])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, b"")


if __name__ == "__main__":
    unittest.main()
