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


def edge(source, target_):
    return {"source": source, "target": target_}


def snapshot(fields, edges):
    return {"fields": fields, "edges": edges}


BASELINE = snapshot(
    {"ods": ["a", "b"], "dwd": ["x"], "ads": ["z"], "dim": ["k"]},
    [
        edge(field("dwd", "x"), field("ods", "a")),
        edge(field("dwd", "x"), field("ods", "b")),
        edge(field("ads", "z"), field("dwd", "x")),
    ],
)

CURRENT = snapshot(
    {"ods": ["a"], "dwd": ["x"], "ads": ["z", "q"], "dim": ["k"]},
    [
        edge(field("dwd", "x"), field("ods", "a")),
        edge(field("ads", "z"), field("dwd", "x")),
        edge(field("ads", "q"), field("ads", "z")),
    ],
)

BASE_PAYLOAD = {
    "baseline": BASELINE,
    "current": CURRENT,
    "targets": [
        field("ods", "a"),
        field("ods", "b"),
        field("ads", "q"),
        field("ads", "z"),
        field("dim", "k"),
    ],
}


class CliLineageDiffSuccessTest(unittest.TestCase):
    def test_diff_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            body,
            {
                "status": "ok",
                "summary": {
                    "added_field_count": 1,
                    "removed_field_count": 1,
                    "added_edge_count": 1,
                    "removed_edge_count": 1,
                    "changed_target_count": 2,
                    "unchanged_target_count": 1,
                },
                "changes": {
                    "field_added": [
                        {"field": field("ads", "q")},
                    ],
                    "field_removed": [
                        {"field": field("ods", "b")},
                    ],
                    "edge_added": [
                        {
                            "edge": edge(field("ads", "q"), field("ads", "z")),
                        },
                    ],
                    "edge_removed": [
                        {
                            "edge": edge(field("dwd", "x"), field("ods", "b")),
                        },
                    ],
                },
                "targets": [
                    {
                        "field": field("ods", "a"),
                        "status": "changed",
                        "upstream_delta": {
                            "added_fields": [field("ads", "q")],
                            "removed_fields": [],
                            "added_edges": [
                                edge(field("ads", "q"), field("ads", "z")),
                            ],
                            "removed_edges": [],
                        },
                        "downstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                    },
                    {
                        "field": field("ods", "b"),
                        "status": "removed",
                        "upstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                        "downstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                    },
                    {
                        "field": field("ads", "q"),
                        "status": "added",
                        "upstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                        "downstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                    },
                    {
                        "field": field("ads", "z"),
                        "status": "changed",
                        "upstream_delta": {
                            "added_fields": [field("ads", "q")],
                            "removed_fields": [],
                            "added_edges": [
                                edge(field("ads", "q"), field("ads", "z")),
                            ],
                            "removed_edges": [],
                        },
                        "downstream_delta": {
                            "added_fields": [],
                            "removed_fields": [field("ods", "b")],
                            "added_edges": [],
                            "removed_edges": [
                                edge(field("dwd", "x"), field("ods", "b")),
                            ],
                        },
                    },
                    {
                        "field": field("dim", "k"),
                        "status": "unchanged",
                        "upstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                        "downstream_delta": {
                            "added_fields": [],
                            "removed_fields": [],
                            "added_edges": [],
                            "removed_edges": [],
                        },
                    },
                ],
            },
        )

    def test_single_line_of_output(self):
        proc = run_json(BASE_PAYLOAD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.decode("utf-8").splitlines()), 1)

    def test_identical_snapshots_still_exit_zero(self):
        payload = {
            "baseline": BASELINE,
            "current": BASELINE,
            "targets": [field("ods", "a")],
        }
        proc = run_json(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(body["status"], "ok")
        self.assertEqual(
            body["changes"],
            {
                "field_added": [],
                "field_removed": [],
                "edge_added": [],
                "edge_removed": [],
            },
        )
        self.assertEqual(body["targets"][0]["status"], "unchanged")

    def test_nothing_written_to_stderr_on_success(self):
        proc = run_json(BASE_PAYLOAD)
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
        self.assertEqual(proc.stderr, b"")

    def test_invalid_json(self):
        self._assert_error(run_cli(b"{nope"), "INVALID_JSON")
        self._assert_error(run_cli(b'{"baseline": \xff}'), "INVALID_JSON")

    def test_payload_not_object(self):
        self._assert_error(run_json([1, 2]), "INVALID_LINEAGE_SNAPSHOT")
        self._assert_error(run_json("snapshots"), "INVALID_LINEAGE_SNAPSHOT")

    def test_invalid_snapshot_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["current"]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["unexpected"] = True
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["baseline"]["edges"]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["current"]["edges"].append(
            edge(field("ods", "a"), field("ods", "phantom"))
        )
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["baseline"]["edges"].append(dict(bad["baseline"]["edges"][0]))
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

    def test_invalid_query_codes(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = []
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = {}
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [field("ods", "a"), field("ods", "a")]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"table": "ods"}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [{"table": "ods", "column": ""}]
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

    def test_unknown_target_code(self):
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = [field("ghost", "a")]
        self._assert_error(run_json(bad), "UNKNOWN_LINEAGE_DIFF_TARGET")

    def test_error_precedence(self):
        self._assert_error(run_cli(b"{"), "INVALID_JSON")

        # Snapshot structure beats targets shape.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        del bad["current"]["fields"]
        bad["targets"] = []
        self._assert_error(run_json(bad), "INVALID_LINEAGE_SNAPSHOT")

        # Targets shape beats unknown existence.
        bad = json.loads(json.dumps(BASE_PAYLOAD))
        bad["targets"] = []
        self._assert_error(run_json(bad), "INVALID_LINEAGE_DIFF_QUERY")

    def test_snapshot_diff_command_unchanged(self):
        # The pre-existing sibling command keeps its own contract.
        proc = subprocess.run(
            [sys.executable, "-m", "data_quality", "snapshot-diff"],
            input=json.dumps(
                {
                    "baseline": {"results": []},
                    "current": {"results": []},
                    "lineage": {"datasets": [], "fields": {}, "edges": []},
                }
            ).encode("utf-8"),
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
