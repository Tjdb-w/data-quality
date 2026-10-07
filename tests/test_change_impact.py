"""Tests for :func:`data_quality.analyze_change_impact`."""

import copy
import unittest

from data_quality import (
    ChangeImpactDatasetNotFoundError,
    ChangeImpactDuplicateFieldError,
    ChangeImpactFieldNotFoundError,
    ChangeImpactInputError,
    ChangeImpactInvalidFieldsError,
    ChangeImpactInvalidRenameError,
    analyze_change_impact,
)


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


def payload(datasets, edges, change):
    return {
        "metadata": {"datasets": datasets, "lineageEdges": edges},
        "change": change,
    }


# ods.name -> dwd.label -> ads.label; ods.id -> dwd.id (typed chain).
BASE_DATASETS = {
    "ods": ["id", "name"],
    "dwd": ["id", "label"],
    "ads": ["label", "note"],
}
BASE_EDGES = [
    col("ods", "name", "dwd", "label"),
    col("dwd", "label", "ads", "label"),
]


def delete(dataset, *fields):
    return {"dataset": dataset, "changeType": "delete", "fields": list(fields)}


def type_change(dataset, *fields):
    return {"dataset": dataset, "changeType": "type_change", "fields": list(fields)}


def rename(dataset, old, new):
    return {
        "dataset": dataset,
        "changeType": "rename",
        "oldField": old,
        "newField": new,
    }


class DeleteImpactTest(unittest.TestCase):
    def test_basic_downstream_chain(self):
        result = analyze_change_impact(
            payload(BASE_DATASETS, BASE_EDGES, delete("ods", "name"))
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["change"], delete("ods", "name"))
        self.assertEqual(
            [item["dataset"] for item in result["affectedDatasets"]],
            ["dwd", "ads"],
        )

        dwd = result["affectedDatasets"][0]
        self.assertEqual(dwd["distance"], 1)
        self.assertEqual(dwd["path"], ["ods", "dwd"])
        self.assertEqual(dwd["fieldImpact"], "known")
        self.assertEqual(
            dwd["affectedFields"],
            [
                {
                    "field": "label",
                    "sources": [ref("ods", "name")],
                    "paths": [[ref("ods", "name"), ref("dwd", "label")]],
                }
            ],
        )

        ads = result["affectedDatasets"][1]
        self.assertEqual(ads["distance"], 2)
        self.assertEqual(ads["path"], ["ods", "dwd", "ads"])
        self.assertEqual(ads["fieldImpact"], "known")
        self.assertEqual(
            ads["affectedFields"][0]["paths"],
            [
                [
                    ref("ods", "name"),
                    ref("dwd", "label"),
                    ref("ads", "label"),
                ]
            ],
        )

    def test_no_downstream_keeps_summary_with_empty_list(self):
        result = analyze_change_impact(
            payload(BASE_DATASETS, BASE_EDGES, delete("ads", "note"))
        )
        self.assertEqual(result["change"], delete("ads", "note"))
        self.assertEqual(result["affectedDatasets"], [])

    def test_upstream_is_never_reported(self):
        # ads.label is downstream; changing it must not report ods/dwd.
        result = analyze_change_impact(
            payload(BASE_DATASETS, BASE_EDGES, delete("ads", "label"))
        )
        self.assertEqual(result["affectedDatasets"], [])

    def test_multiple_entry_fields_preserve_each_source(self):
        datasets = {
            "a": ["x", "y"],
            "b": ["z"],
        }
        edges = [
            col("a", "x", "b", "z"),
            col("a", "y", "b", "z"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x", "y"))
        )
        hit = result["affectedDatasets"][0]["affectedFields"][0]
        # Sources and their paths are kept separately, source refs sorted.
        self.assertEqual(hit["sources"], [ref("a", "x"), ref("a", "y")])
        self.assertEqual(
            hit["paths"],
            [
                [ref("a", "x"), ref("b", "z")],
                [ref("a", "y"), ref("b", "z")],
            ],
        )

    def test_unrelated_entry_field_is_silent(self):
        datasets = {"a": ["x", "y"], "b": ["z"]}
        edges = [col("a", "y", "b", "z")]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x", "y"))
        )
        hit = result["affectedDatasets"][0]["affectedFields"][0]
        self.assertEqual(hit["sources"], [ref("a", "y")])

    def test_change_summary_echoes_request_field_order(self):
        result = analyze_change_impact(
            payload(BASE_DATASETS, BASE_EDGES, delete("ods", "name", "id"))
        )
        self.assertEqual(result["change"]["fields"], ["name", "id"])


class OrderingAndPathTest(unittest.TestCase):
    def test_same_distance_sorts_by_dataset_id(self):
        datasets = {"a": ["x"], "b": ["p"], "c": ["q"], "d": ["r"]}
        edges = [
            col("a", "x", "c", "q"),
            col("a", "x", "b", "p"),
            col("a", "x", "d", "r"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        self.assertEqual(
            [item["dataset"] for item in result["affectedDatasets"]],
            ["b", "c", "d"],
        )

    def test_dataset_path_lexicographic_tie_break(self):
        # a -> b and a -> m -> b have equal length only in a diamond:
        # a -> {n, z} -> b: shortest path must pick the n route.
        datasets = {"a": ["x"], "n": ["u"], "z": ["u"], "b": ["y"]}
        edges = [
            col("a", "x", "n", "u"),
            col("a", "x", "z", "u"),
            col("n", "u", "b", "y"),
            col("z", "u", "b", "y"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        by_ds = {item["dataset"]: item for item in result["affectedDatasets"]}
        self.assertEqual(by_ds["n"]["path"], ["a", "n"])
        self.assertEqual(by_ds["z"]["path"], ["a", "z"])
        self.assertEqual(by_ds["b"]["path"], ["a", "n", "b"])
        # The field chain honors the same tie.
        self.assertEqual(
            by_ds["b"]["affectedFields"][0]["paths"],
            [[ref("a", "x"), ref("n", "u"), ref("b", "y")]],
        )

    def test_field_path_tie_break_uses_full_names(self):
        # Target t.v can be reached at equal length via aa or via a; the
        # full-name sequence "a.x,..." beats "aa.x,...".
        datasets = {
            "a": ["x"], "aa": ["x"], "m": ["u"], "t": ["v"],
        }
        edges = [
            col("a", "x", "m", "u"),
            col("aa", "x", "m", "u"),
            col("m", "u", "t", "v"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        t = next(i for i in result["affectedDatasets"] if i["dataset"] == "t")
        self.assertEqual(
            t["affectedFields"][0]["paths"],
            [[ref("a", "x"), ref("m", "u"), ref("t", "v")]],
        )


class DatasetLevelAndUnknownTest(unittest.TestCase):
    def test_dataset_only_edge_is_unknown_with_empty_fields(self):
        datasets = dict(BASE_DATASETS, rpt=["x"])
        result = analyze_change_impact(
            payload(datasets, BASE_EDGES + [rel("ads", "rpt")],
                    delete("ods", "name"))
        )
        rpt = next(
            item for item in result["affectedDatasets"]
            if item["dataset"] == "rpt"
        )
        self.assertEqual(rpt["distance"], 3)
        self.assertEqual(rpt["path"], ["ods", "dwd", "ads", "rpt"])
        self.assertEqual(rpt["fieldImpact"], "unknown")
        self.assertEqual(rpt["affectedFields"], [])

    def test_dataset_level_shortcut_does_not_become_known(self):
        # Direct dataset edge ods -> rpt must not guess rpt fields.
        edges = BASE_EDGES + [rel("ods", "rpt")]
        datasets = dict(BASE_DATASETS, rpt=["anything", "name"])
        result = analyze_change_impact(
            payload(datasets, edges, delete("ods", "name"))
        )
        rpt = next(
            item for item in result["affectedDatasets"]
            if item["dataset"] == "rpt"
        )
        self.assertEqual(rpt["fieldImpact"], "unknown")
        self.assertEqual(rpt["affectedFields"], [])
        self.assertEqual(rpt["path"], ["ods", "rpt"])
        self.assertEqual(rpt["distance"], 1)

    def test_known_and_unknown_same_distance_sort_by_id(self):
        datasets = {
            "a": ["x"], "k1": ["y"], "u1": ["y"], "u2": ["y"],
        }
        edges = [
            col("a", "x", "u2", "y"),  # column edge: makes u2 actually known
            rel("a", "u1"),
            col("a", "x", "k1", "y"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        rows = result["affectedDatasets"]
        self.assertEqual([r["dataset"] for r in rows], ["k1", "u1", "u2"])
        self.assertEqual(rows[0]["fieldImpact"], "known")
        self.assertEqual(rows[1]["fieldImpact"], "unknown")
        self.assertEqual(rows[2]["fieldImpact"], "known")

    def test_nested_edge_shapes_are_equivalent(self):
        datasets = {"a": ["x"], "b": ["y"], "c": ["z"]}
        nested = [
            {"source": {"dataset": "a", "field": "x"},
             "target": {"dataset": "b", "field": "y"}},
            {"source": {"dataset": "b"}, "target": {"dataset": "c"}},
        ]
        result = analyze_change_impact(
            payload(datasets, nested, delete("a", "x"))
        )
        by_ds = {item["dataset"]: item for item in result["affectedDatasets"]}
        self.assertEqual(by_ds["b"]["fieldImpact"], "known")
        self.assertEqual(by_ds["c"]["fieldImpact"], "unknown")


class RenameTest(unittest.TestCase):
    def test_rename_considers_old_deletion_and_new_dependencies(self):
        datasets = {"a": ["old", "new"], "b": ["z"]}
        edges = [
            col("a", "old", "b", "z"),
            col("a", "new", "b", "z"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, rename("a", "old", "new"))
        )
        # Exactly one change summary, the rename shape.
        self.assertEqual(
            result["change"],
            {"dataset": "a", "changeType": "rename",
             "oldField": "old", "newField": "new"},
        )
        hit = result["affectedDatasets"][0]["affectedFields"][0]
        self.assertEqual(hit["sources"], [ref("a", "new"), ref("a", "old")])
        self.assertEqual(
            hit["paths"],
            [
                [ref("a", "new"), ref("b", "z")],
                [ref("a", "old"), ref("b", "z")],
            ],
        )

    def test_rename_only_old_side_propagates(self):
        datasets = {"a": ["old", "new"], "b": ["z"], "c": ["w"]}
        edges = [
            col("a", "old", "b", "z"),
            col("b", "z", "c", "w"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, rename("a", "old", "new"))
        )
        datasets_hit = [item["dataset"] for item in result["affectedDatasets"]]
        self.assertEqual(datasets_hit, ["b", "c"])
        hit = result["affectedDatasets"][0]["affectedFields"][0]
        self.assertEqual(hit["sources"], [ref("a", "old")])

    def test_rename_with_no_downstream_is_empty(self):
        # Neither the old nor the new field has a registered outgoing edge.
        result = analyze_change_impact(
            payload({"a": ["x", "y"]}, [], rename("a", "x", "y"))
        )
        self.assertEqual(result["affectedDatasets"], [])


class TypeChangeTest(unittest.TestCase):
    def test_same_raw_type_produces_no_impact(self):
        datasets = {
            "a": [{"field": "x", "type": "string"}],
            "b": [{"field": "y", "type": "string"}],
        }
        edges = [col("a", "x", "b", "y")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "x"))
        )
        self.assertEqual(result["affectedDatasets"], [])

    def test_different_raw_type_is_reported(self):
        datasets = {
            "a": [{"field": "x", "type": "string"}],
            "b": [{"field": "y", "type": "int64"}],
        }
        edges = [col("a", "x", "b", "y")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "x"))
        )
        self.assertEqual(
            [item["dataset"] for item in result["affectedDatasets"]], ["b"]
        )

    def test_missing_types_compare_equal(self):
        datasets = {"a": ["x"], "b": ["y"]}
        edges = [col("a", "x", "b", "y")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "x"))
        )
        self.assertEqual(result["affectedDatasets"], [])

    def test_raw_type_value_preserved_verbatim(self):
        datasets = {
            "a": [{"field": "x", "type": "VARCHAR(10)"}],
            "b": [{"field": "y", "type": "VARCHAR(20)"}],
        }
        edges = [col("a", "x", "b", "y")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "x"))
        )
        self.assertEqual(len(result["affectedDatasets"]), 1)

    def test_per_source_type_filtering(self):
        # b.z has the same type as a.x but differs from a.w; z depends on
        # both entry fields, so only the w source survives.
        datasets = {
            "a": [{"field": "x", "type": "string"},
                  {"field": "w", "type": "int"}],
            "b": [{"field": "z", "type": "string"}],
        }
        edges = [col("a", "x", "b", "z"), col("a", "w", "b", "z")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "w", "x"))
        )
        hit = result["affectedDatasets"][0]["affectedFields"][0]
        self.assertEqual(hit["sources"], [ref("a", "w")])

    def test_filtered_column_then_dataset_edge_is_unknown(self):
        # b.y keeps the same raw type, so b itself is not affected; but a
        # genuine dataset-level edge b -> r leaves the column region and r
        # is reported unknown.
        datasets = {
            "a": [{"field": "x", "type": "string"}],
            "b": [{"field": "y", "type": "string"}],
            "r": ["q"],
        }
        edges = [col("a", "x", "b", "y"), rel("b", "r")]
        result = analyze_change_impact(
            payload(datasets, edges, type_change("a", "x"))
        )
        rows = result["affectedDatasets"]
        self.assertEqual([row["dataset"] for row in rows], ["r"])
        self.assertEqual(rows[0]["fieldImpact"], "unknown")
        self.assertEqual(rows[0]["affectedFields"], [])
        self.assertEqual(rows[0]["path"], ["a", "b", "r"])
        self.assertEqual(rows[0]["distance"], 2)


class CycleTest(unittest.TestCase):
    def test_self_loop_and_cycle_do_not_trap_or_repeat_nodes(self):
        datasets = {"a": ["x", "y"], "b": ["z"], "c": ["q"]}
        edges = [
            col("a", "x", "b", "z"),
            col("b", "z", "a", "x"),    # cycle back to the seed
            col("a", "y", "a", "y"),    # self-loop on another field
            col("b", "z", "c", "q"),
        ]
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        by_ds = {item["dataset"]: item for item in result["affectedDatasets"]}
        # The seed dataset is not reported as affected through the back-edge.
        self.assertNotIn("a", by_ds)
        q_path = by_ds["c"]["affectedFields"][0]["paths"][0]
        self.assertEqual(
            q_path, [ref("a", "x"), ref("b", "z"), ref("c", "q")]
        )
        # Every reported path is simple (no repeated node).
        for item in result["affectedDatasets"]:
            for field in item["affectedFields"]:
                for p in field["paths"]:
                    self.assertEqual(len(p), len({tuple(s.items()) for s in p}))

    def test_repeated_calls_are_deterministic(self):
        datasets = {"a": ["x"], "n": ["u"], "z": ["u"], "b": ["y"]}
        edges = [
            col("a", "x", "z", "u"),
            col("a", "x", "n", "u"),
            col("z", "u", "b", "y"),
            col("n", "u", "b", "y"),
        ]
        request = payload(datasets, edges, delete("a", "x"))
        first = analyze_change_impact(copy.deepcopy(request))
        second = analyze_change_impact(copy.deepcopy(request))
        self.assertEqual(first, second)

    def test_input_is_not_mutated(self):
        request = payload(BASE_DATASETS, BASE_EDGES, delete("ods", "name"))
        snapshot = copy.deepcopy(request)
        analyze_change_impact(request)
        self.assertEqual(request, snapshot)


class CaseSensitivityTest(unittest.TestCase):
    def test_identifiers_are_case_sensitive(self):
        datasets = {"a": ["x"], "A": ["x"]}
        edges = [col("A", "x", "a", "x")]
        # Lowercase entry field has no downstream edge.
        result = analyze_change_impact(
            payload(datasets, edges, delete("a", "x"))
        )
        self.assertEqual(result["affectedDatasets"], [])
        # Uppercase entry reaches the lowercase dataset.
        result = analyze_change_impact(
            payload(datasets, edges, delete("A", "x"))
        )
        self.assertEqual(
            [item["dataset"] for item in result["affectedDatasets"]], ["a"]
        )


class BusinessErrorTest(unittest.TestCase):
    def _assert_code(self, request, code):
        with self.assertRaises(ValueError) as ctx:
            analyze_change_impact(request)
        self.assertEqual(getattr(ctx.exception, "code", None), code)

    def test_dataset_not_found(self):
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, delete("ghost", "name")),
            "DATASET_NOT_FOUND",
        )
        with self.assertRaises(ChangeImpactDatasetNotFoundError):
            analyze_change_impact(
                payload(BASE_DATASETS, BASE_EDGES, delete("ghost", "name"))
            )

    def test_field_not_found(self):
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, delete("ods", "ghost")),
            "FIELD_NOT_FOUND",
        )
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, rename("ods", "name", "ghost")),
            "FIELD_NOT_FOUND",
        )
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, rename("ods", "ghost", "name")),
            "FIELD_NOT_FOUND",
        )

    def test_invalid_rename_same_name(self):
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, rename("ods", "name", "name")),
            "INVALID_RENAME",
        )

    def test_duplicate_field(self):
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, delete("ods", "name", "name")),
            "DUPLICATE_FIELD",
        )

    def test_invalid_fields_empty(self):
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, delete("ods")),
            "INVALID_FIELDS",
        )

    def test_error_precedence(self):
        # Empty fields beat an unknown dataset.
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, delete("ghost")),
            "INVALID_FIELDS",
        )
        # Unknown dataset beats a rename equality problem.
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, rename("ghost", "x", "x")),
            "DATASET_NOT_FOUND",
        )
        # Rename equality beats field existence.
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES, rename("ods", "x", "x")),
            "INVALID_RENAME",
        )
        # Duplicate beats not-found on delete.
        self._assert_code(
            payload(BASE_DATASETS, BASE_EDGES,
                    delete("ods", "ghost", "ghost")),
            "DUPLICATE_FIELD",
        )

    def test_errors_are_business_subclasses(self):
        for change, expected in (
            (delete("ghost", "x"), ChangeImpactDatasetNotFoundError),
            (delete("ods", "ghost"), ChangeImpactFieldNotFoundError),
            (rename("ods", "x", "x"), ChangeImpactInvalidRenameError),
            (delete("ods", "x", "x"), ChangeImpactDuplicateFieldError),
            (delete("ods"), ChangeImpactInvalidFieldsError),
        ):
            with self.assertRaises(expected):
                analyze_change_impact(
                    payload(BASE_DATASETS, BASE_EDGES, change)
                )


class StructuralErrorTest(unittest.TestCase):
    def test_payload_shape(self):
        for bad in (None, [], "x", 42):
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(bad)
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact({"metadata": {}})
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {
                    "metadata": {
                        "datasets": BASE_DATASETS,
                        "lineageEdges": BASE_EDGES,
                    },
                    "change": delete("ods", "name"),
                    "extra": 1,
                }
            )

    def test_metadata_shape(self):
        good_change = delete("ods", "name")
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact({"metadata": [], "change": good_change})
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS},
                 "change": good_change}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": [], "lineageEdges": BASE_EDGES,
                              "other": 1},
                 "change": good_change}
            )

    def test_dataset_declarations(self):
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload({"": ["x"]}, [], delete("", "x"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload({"a": "x"}, [], delete("a", "x"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload({"a": [""]}, [], delete("a", "x"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload({"a": [{"field": "x"}]}, [], delete("a", "x"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload({"a": ["x", "x"]}, [], delete("a", "x"))
            )

    def test_edge_declarations(self):
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload(BASE_DATASETS, "edges", delete("ods", "name"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload(BASE_DATASETS, [{"sourceDataset": "ods"}],
                        delete("ods", "name"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload(BASE_DATASETS, [rel("ghost", "ads")],
                        delete("ods", "name"))
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload(BASE_DATASETS,
                        [col("ods", "ghost", "ads", "label")],
                        delete("ods", "name"))
            )
        # Mixed granularity in one nested edge.
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                payload(
                    BASE_DATASETS,
                    [{"source": {"dataset": "ods", "field": "name"},
                      "target": {"dataset": "ads"}}],
                    delete("ods", "name"),
                )
            )

    def test_change_shape(self):
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": "ods"}}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": "ods", "changeType": "drop",
                            "fields": ["name"]}}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": "ods", "changeType": "delete",
                            "fields": {}}}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": "ods", "changeType": "delete",
                            "fields": ["name"], "oldField": "x"}}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": 1, "changeType": "delete",
                            "fields": ["name"]}}
            )
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(
                {"metadata": {"datasets": BASE_DATASETS,
                              "lineageEdges": BASE_EDGES},
                 "change": {"dataset": "ods", "changeType": "rename",
                            "oldField": "x"}}
            )


if __name__ == "__main__":
    unittest.main()
