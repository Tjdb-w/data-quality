"""Tests for :func:`data_quality.analyze_change_impact`."""

import copy
import json
import unittest

from data_quality import (
    ChangeImpactInputError,
    DatasetNotFoundError,
    DuplicateFieldError,
    FieldNotFoundError,
    InvalidFieldsError,
    InvalidRenameError,
    analyze_change_impact,
)


def fref(dataset, field):
    return {"dataset": dataset, "field": field}


def dref(dataset):
    return {"dataset": dataset}


def cedge(sd, sf, td, tf):
    return {
        "source": fref(sd, sf),
        "target": fref(td, tf),
    }


def dedge(sd, td):
    return {"source": dref(sd), "target": dref(td)}


def decl(names, type_="string"):
    return [{"name": name, "type": type_} for name in names]


# ods.name/full_name -> dwd.label -> ads.out and b.m; dwd.name is isolated.
METADATA = {
    "datasets": {
        "ods": decl(["id", "name", "full_name"]),
        "dwd": decl(["id", "label", "name"]),
        "ads": decl(["out", "zz"]),
        "b": decl(["m"]),
        "rpt": decl(["x"]),
    },
    "edges": [
        cedge("ods", "full_name", "dwd", "label"),
        cedge("ods", "name", "dwd", "label"),
        cedge("dwd", "label", "ads", "out"),
        cedge("dwd", "label", "b", "m"),
        dedge("ads", "rpt"),
    ],
}


def delete(dataset, fields, metadata=METADATA):
    return {
        "dataset": dataset,
        "changeType": "delete",
        "fields": list(fields),
        "metadata": metadata,
    }


class DeleteImpactTest(unittest.TestCase):
    def test_proven_fields_sources_paths_and_unknown_tail(self):
        result = analyze_change_impact(copy.deepcopy(delete("ods", ["name"])))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["change"],
            {"dataset": "ods", "changeType": "delete", "fields": ["name"]},
        )
        by_ds = {d["dataset"]: d for d in result["affectedDatasets"]}

        # Ordering: distance ascending, then dataset id.
        self.assertEqual(
            [(d["distance"], d["dataset"]) for d in result["affectedDatasets"]],
            [(1, "dwd"), (2, "ads"), (2, "b"), (3, "rpt")],
        )

        dwd = by_ds["dwd"]
        self.assertFalse(dwd["unknown"])
        self.assertEqual(dwd["path"], [dref("ods"), dref("dwd")])
        self.assertEqual(len(dwd["fields"]), 1)
        self.assertEqual(dwd["fields"][0]["field"], "label")
        self.assertEqual(
            dwd["fields"][0]["sources"],
            [
                {
                    "dataset": "ods",
                    "field": "name",
                    "path": [fref("ods", "name"), fref("dwd", "label")],
                }
            ],
        )

        ads = by_ds["ads"]
        self.assertFalse(ads["unknown"])
        self.assertEqual(
            ads["path"], [dref("ods"), dref("dwd"), dref("ads")]
        )
        self.assertEqual(
            ads["fields"][0]["sources"][0]["path"],
            [
                fref("ods", "name"),
                fref("dwd", "label"),
                fref("ads", "out"),
            ],
        )

        # ads.zz is never guessed as affected even though ads is reached.
        self.assertEqual([f["field"] for f in ads["fields"]], ["out"])

        # The dataset-level ads -> rpt edge keeps propagating with no
        # provable column: rpt is unknown with an empty field list.
        rpt = by_ds["rpt"]
        self.assertTrue(rpt["unknown"])
        self.assertEqual(rpt["fields"], [])
        self.assertEqual(
            rpt["path"],
            [dref("ods"), dref("dwd"), dref("ads"), dref("rpt")],
        )

    def test_multiple_entry_fields_keep_separate_sources(self):
        result = analyze_change_impact(
            copy.deepcopy(delete("ods", ["name", "full_name", "id"]))
        )
        dwd = next(d for d in result["affectedDatasets"] if d["dataset"] == "dwd")
        label = dwd["fields"][0]
        # dwd.label depends on both changed columns; id is isolated and
        # produces no provenance. Both sources survive, source-sorted.
        self.assertEqual(
            [(s["dataset"], s["field"]) for s in label["sources"]],
            [("ods", "full_name"), ("ods", "name")],
        )

    def test_unrelated_entry_column_has_no_downstream(self):
        result = analyze_change_impact(copy.deepcopy(delete("dwd", ["name"])))
        # No column edge leaves dwd.name, and other columns' edges are
        # not projected on its behalf: there is no affected dataset at all.
        self.assertEqual(result["affectedDatasets"], [])
        self.assertEqual(
            result["change"],
            {"dataset": "dwd", "changeType": "delete", "fields": ["name"]},
        )

    def test_no_downstream_keeps_entry_summary(self):
        metadata = {
            "datasets": {"a": decl(["x"]), "b": decl(["y"])},
            "edges": [],
        }
        result = analyze_change_impact(copy.deepcopy(delete("a", ["x"], metadata)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["affectedDatasets"], [])
        self.assertEqual(result["change"]["dataset"], "a")

    def test_only_downstream_is_entry_dataset_self_loop(self):
        metadata = {
            "datasets": {"a": decl(["x"])},
            "edges": [cedge("a", "x", "a", "x")],
        }
        result = analyze_change_impact(copy.deepcopy(delete("a", ["x"], metadata)))
        self.assertEqual(len(result["affectedDatasets"]), 1)
        ds = result["affectedDatasets"][0]
        self.assertEqual(ds["dataset"], "a")
        self.assertEqual(ds["distance"], 1)
        self.assertFalse(ds["unknown"])
        self.assertEqual(
            ds["path"], [dref("a"), dref("a")]
        )
        self.assertEqual(
            ds["fields"][0]["sources"][0]["path"],
            [fref("a", "x"), fref("a", "x")],
        )

    def test_cycle_terminates_and_stays_shortest(self):
        metadata = {
            "datasets": {
                "a": decl(["x"]),
                "b": decl(["y"]),
                "c": decl(["z"]),
            },
            "edges": [
                cedge("a", "x", "b", "y"),
                cedge("b", "y", "a", "x"),
                cedge("b", "y", "c", "z"),
            ],
        }
        result = analyze_change_impact(copy.deepcopy(delete("a", ["x"], metadata)))
        by_ds = {d["dataset"]: d for d in result["affectedDatasets"]}
        # Shortest distances ignore the cycle; the path back to a is via b.
        self.assertEqual(by_ds["b"]["distance"], 1)
        self.assertEqual(by_ds["c"]["distance"], 2)
        self.assertEqual(by_ds["a"]["distance"], 2)
        self.assertEqual(
            by_ds["a"]["path"], [dref("a"), dref("b"), dref("a")]
        )
        self.assertEqual(
            by_ds["c"]["fields"][0]["sources"][0]["path"],
            [fref("a", "x"), fref("b", "y"), fref("c", "z")],
        )

    def test_dataset_self_loop_is_affected(self):
        metadata = {
            "datasets": {"a": decl(["x"])},
            "edges": [dedge("a", "a")],
        }
        result = analyze_change_impact(copy.deepcopy(delete("a", ["x"], metadata)))
        self.assertEqual(len(result["affectedDatasets"]), 1)
        ds = result["affectedDatasets"][0]
        self.assertEqual(ds["dataset"], "a")
        self.assertTrue(ds["unknown"])
        self.assertEqual(ds["distance"], 1)
        self.assertEqual(ds["path"], [dref("a"), dref("a")])

    def test_lexicographic_path_tie_break(self):
        # Two equal-length dataset paths dwd1/r and dwd2/r from ods; the
        # lexicographically smaller node sequence wins.
        metadata = {
            "datasets": {
                "ods": decl(["x"]),
                "dwd1": decl(["x"]),
                "dwd2": decl(["x"]),
                "r": decl(["z"]),
            },
            "edges": [
                cedge("ods", "x", "dwd1", "x"),
                cedge("ods", "x", "dwd2", "x"),
                cedge("dwd1", "x", "r", "z"),
                cedge("dwd2", "x", "r", "z"),
            ],
        }
        result = analyze_change_impact(copy.deepcopy(delete("ods", ["x"], metadata)))
        r_ds = next(d for d in result["affectedDatasets"] if d["dataset"] == "r")
        self.assertEqual(
            r_ds["path"], [dref("ods"), dref("dwd1"), dref("r")]
        )
        # Both seeds are the same column, so exactly one source remains;
        # its column path also breaks ties lexicographically.
        source_path = r_ds["fields"][0]["sources"][0]["path"]
        self.assertEqual(
            source_path,
            [fref("ods", "x"), fref("dwd1", "x"), fref("r", "z")],
        )

    def test_upstream_is_never_reported(self):
        metadata = {
            "datasets": {
                "up": decl(["q"]),
                "ods": decl(["name"]),
                "dwd": decl(["label"]),
            },
            "edges": [
                cedge("up", "q", "ods", "name"),
                cedge("ods", "name", "dwd", "label"),
            ],
        }
        result = analyze_change_impact(copy.deepcopy(delete("ods", ["name"], metadata)))
        self.assertEqual(
            [d["dataset"] for d in result["affectedDatasets"]], ["dwd"]
        )


class RenameImpactTest(unittest.TestCase):
    def rename(self, old, new, metadata=METADATA):
        return {
            "dataset": "ods",
            "changeType": "rename",
            "fields": {"oldField": old, "newField": new},
            "metadata": metadata,
        }

    def test_rename_combines_old_and_new_downstream_one_summary(self):
        result = analyze_change_impact(
            copy.deepcopy(self.rename("name", "full_name"))
        )
        self.assertEqual(
            result["change"],
            {
                "dataset": "ods",
                "changeType": "rename",
                "oldField": "name",
                "newField": "full_name",
            },
        )
        dwd = next(d for d in result["affectedDatasets"] if d["dataset"] == "dwd")
        # Both old and new fields feed dwd.label; sources keep both.
        self.assertEqual(
            [(s["dataset"], s["field"]) for s in dwd["fields"][0]["sources"]],
            [("ods", "full_name"), ("ods", "name")],
        )

    def test_rename_to_unregistered_new_name_uses_old_only(self):
        result = analyze_change_impact(
            copy.deepcopy(self.rename("name", "brand_new"))
        )
        dwd = next(d for d in result["affectedDatasets"] if d["dataset"] == "dwd")
        self.assertEqual(
            [s["field"] for s in dwd["fields"][0]["sources"]],
            ["name"],
        )

    def test_rename_old_field_missing(self):
        with self.assertRaises(FieldNotFoundError) as ctx:
            analyze_change_impact(copy.deepcopy(self.rename("ghost", "name")))
        self.assertEqual(ctx.exception.code, "FIELD_NOT_FOUND")

    def test_rename_same_name(self):
        with self.assertRaises(InvalidRenameError) as ctx:
            analyze_change_impact(copy.deepcopy(self.rename("name", "name")))
        self.assertEqual(ctx.exception.code, "INVALID_RENAME")

    def test_rename_unknown_dataset(self):
        payload = self.rename("a", "b")
        payload["dataset"] = "ghost"
        with self.assertRaises(DatasetNotFoundError) as ctx:
            analyze_change_impact(copy.deepcopy(payload))
        self.assertEqual(ctx.exception.code, "DATASET_NOT_FOUND")


class TypeChangeImpactTest(unittest.TestCase):
    def type_change(self, dataset, fields, new_type, metadata):
        return {
            "dataset": dataset,
            "changeType": "type_change",
            "fields": list(fields),
            "newType": new_type,
            "metadata": metadata,
        }

    def test_changed_type_propagates_and_echoes_new_type(self):
        result = analyze_change_impact(
            copy.deepcopy(self.type_change("ods", ["name"], "varchar(64)", METADATA))
        )
        self.assertEqual(
            result["change"],
            {
                "dataset": "ods",
                "changeType": "type_change",
                "fields": ["name"],
                "newType": "varchar(64)",
            },
        )
        self.assertEqual(
            [d["dataset"] for d in result["affectedDatasets"]],
            ["dwd", "ads", "b", "rpt"],
        )

    def test_same_raw_type_has_no_impact(self):
        result = analyze_change_impact(
            copy.deepcopy(self.type_change("ods", ["name"], "string", METADATA))
        )
        self.assertEqual(result["change"]["fields"], [])
        self.assertEqual(result["change"]["newType"], "string")
        self.assertEqual(result["affectedDatasets"], [])

    def test_partial_type_change_filters_unchanged_fields(self):
        result = analyze_change_impact(
            copy.deepcopy(
                self.type_change(
                    "ods", ["name", "full_name"], "varchar(64)", METADATA
                )
            )
        )
        # The change summary keeps the request field order.
        self.assertEqual(result["change"]["fields"], ["name", "full_name"])

        same = analyze_change_impact(
            copy.deepcopy(
                self.type_change("ods", ["name", "id"], "string", METADATA)
            )
        )
        # Nothing changed: neither column propagates.
        self.assertEqual(same["affectedDatasets"], [])
        self.assertEqual(same["change"]["fields"], [])

    def test_bool_is_not_equal_to_number_type(self):
        metadata = {
            "datasets": {"a": decl(["x"], type_=True)},
            "edges": [],
        }
        changed = analyze_change_impact(
            copy.deepcopy(self.type_change("a", ["x"], 1, metadata))
        )
        self.assertEqual(changed["change"]["fields"], ["x"])
        same = analyze_change_impact(
            copy.deepcopy(self.type_change("a", ["x"], True, metadata))
        )
        self.assertEqual(same["change"]["fields"], [])

    def test_raw_type_value_is_preserved(self):
        metadata = {
            "datasets": {"a": [{"name": "x", "type": {"kind": "varchar"}}]},
            "edges": [],
        }
        result = analyze_change_impact(
            copy.deepcopy(
                self.type_change("a", ["x"], {"kind": "decimal"}, metadata)
            )
        )
        self.assertEqual(result["change"]["newType"], {"kind": "decimal"})


class ErrorTest(unittest.TestCase):
    def _assert_code(self, payload, exception, code=None):
        with self.assertRaises(exception) as ctx:
            analyze_change_impact(copy.deepcopy(payload))
        if code is not None:
            self.assertEqual(ctx.exception.code, code)

    def test_dataset_not_found(self):
        self._assert_code(
            delete("ghost", ["name"]), DatasetNotFoundError, "DATASET_NOT_FOUND"
        )

    def test_field_not_found(self):
        self._assert_code(
            delete("ods", ["ghost"]), FieldNotFoundError, "FIELD_NOT_FOUND"
        )

    def test_duplicate_field(self):
        self._assert_code(
            delete("ods", ["name", "name"]),
            DuplicateFieldError,
            "DUPLICATE_FIELD",
        )

    def test_empty_fields(self):
        self._assert_code(
            delete("ods", []), InvalidFieldsError, "INVALID_FIELDS"
        )

    def test_bad_change_type(self):
        payload = delete("ods", ["name"])
        payload["changeType"] = "drop"
        self._assert_code(
            payload, ChangeImpactInputError, "INVALID_CHANGE_IMPACT_INPUT"
        )

    def test_payload_not_object(self):
        for bad in ([], "x", 42, None):
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(bad)

    def test_missing_and_extra_top_level_keys(self):
        payload = delete("ods", ["name"])
        for key in ("dataset", "changeType", "fields", "metadata"):
            bad = copy.deepcopy(payload)
            del bad[key]
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(bad)
        bad = copy.deepcopy(payload)
        bad["extra"] = 1
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(bad)

    def test_type_change_requires_new_type(self):
        payload = delete("ods", ["name"])
        payload["changeType"] = "type_change"
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(payload)
        payload["newType"] = "t"
        analyze_change_impact(payload)  # well-formed now

    def test_type_change_rejects_new_type_on_delete(self):
        payload = delete("ods", ["name"])
        payload["newType"] = "t"
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(payload)

    def test_dataset_must_be_nonempty_string(self):
        payload = delete("ods", ["name"])
        for bad in (1, "", None):
            wrong = copy.deepcopy(payload)
            wrong["dataset"] = bad
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(wrong)

    def test_fields_wrong_shape(self):
        payload = delete("ods", ["name"])
        for bad in ("name", {}, [1], [""]):
            wrong = copy.deepcopy(payload)
            wrong["fields"] = bad
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(wrong)

    def test_rename_fields_wrong_shape(self):
        good = {
            "dataset": "ods",
            "changeType": "rename",
            "fields": {"oldField": "name", "newField": "full_name"},
            "metadata": METADATA,
        }
        for bad in (
            ["name", "full_name"],
            {"oldField": "name"},
            {"oldField": "name", "newField": "id", "extra": 1},
            {"oldField": "", "newField": "id"},
            {"oldField": "name", "newField": 1},
        ):
            wrong = copy.deepcopy(good)
            wrong["fields"] = bad
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(wrong)

    def test_metadata_malformed(self):
        payload = delete("ods", ["name"])
        for bad in (
            [],
            {"datasets": []},
            {"edges": []},
            {"datasets": [], "edges": [], "x": 1},
        ):
            wrong = copy.deepcopy(payload)
            wrong["metadata"] = bad
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(wrong)

    def test_dataset_declaration_malformed(self):
        payload = delete("ods", ["name"])
        bad_datasets = [
            {"ods": ["name"]},  # field decl must be objects
            {"ods": [{"name": "name"}]},  # missing type
            {"ods": [{"name": "name", "type": "s", "x": 1}]},  # extra key
            {"ods": [{"name": "", "type": "s"}]},  # empty field name
            {"ods": decl(["x", "x"])},  # duplicate field declaration
            {"": decl(["x"])},  # empty dataset id
        ]
        for datasets in bad_datasets:
            wrong = copy.deepcopy(payload)
            wrong["metadata"] = {"datasets": datasets, "edges": []}
            with self.assertRaises(ChangeImpactInputError):
                analyze_change_impact(wrong)

    def test_edge_malformed(self):
        payload = delete("ods", ["name"])
        # Edge referencing undeclared dataset/field.
        wrong = copy.deepcopy(payload)
        wrong["metadata"] = {
            "datasets": copy.deepcopy(METADATA["datasets"]),
            "edges": [cedge("ods", "name", "ghost", "label")],
        }
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(wrong)
        # Mixed granularity endpoints.
        wrong = copy.deepcopy(payload)
        wrong["metadata"] = {
            "datasets": copy.deepcopy(METADATA["datasets"]),
            "edges": [
                {
                    "source": fref("ods", "name"),
                    "target": dref("dwd"),
                }
            ],
        }
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(wrong)
        # Duplicate edges.
        wrong = copy.deepcopy(payload)
        wrong["metadata"] = {
            "datasets": copy.deepcopy(METADATA["datasets"]),
            "edges": [
                cedge("ods", "name", "dwd", "label"),
                cedge("ods", "name", "dwd", "label"),
            ],
        }
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(wrong)
        # Edge with missing/extra keys.
        wrong = copy.deepcopy(payload)
        wrong["metadata"] = {
            "datasets": copy.deepcopy(METADATA["datasets"]),
            "edges": [{"source": fref("ods", "name")}],
        }
        with self.assertRaises(ChangeImpactInputError):
            analyze_change_impact(wrong)

    def test_error_precedence_empty_fields_before_dataset(self):
        # Empty field set is rejected before dataset existence.
        payload = delete("ghost", [])
        with self.assertRaises(InvalidFieldsError):
            analyze_change_impact(payload)

    def test_error_precedence_rename_before_dataset(self):
        payload = {
            "dataset": "ghost",
            "changeType": "rename",
            "fields": {"oldField": "a", "newField": "a"},
            "metadata": METADATA,
        }
        with self.assertRaises(InvalidRenameError):
            analyze_change_impact(payload)

    def test_error_precedence_dataset_before_field(self):
        payload = delete("ghost", ["nope"])
        with self.assertRaises(DatasetNotFoundError):
            analyze_change_impact(payload)

    def test_no_partial_result(self):
        with self.assertRaises(FieldNotFoundError):
            analyze_change_impact(delete("ods", ["nope"]))


class DeterminismAndImmutabilityTest(unittest.TestCase):
    def test_repeatable_and_input_unchanged(self):
        payload = delete("ods", ["name"])
        snapshot = copy.deepcopy(payload)
        first = json.dumps(
            analyze_change_impact(copy.deepcopy(payload)),
            sort_keys=True,
            ensure_ascii=False,
        )
        second = json.dumps(
            analyze_change_impact(copy.deepcopy(payload)),
            sort_keys=True,
            ensure_ascii=False,
        )
        self.assertEqual(first, second)
        self.assertEqual(payload, snapshot)

    def test_case_sensitive_identifiers(self):
        # "ODS" is a different, undeclared dataset even though "ods" is.
        with self.assertRaises(DatasetNotFoundError):
            analyze_change_impact(delete("ODS", ["name"]))


if __name__ == "__main__":
    unittest.main()
