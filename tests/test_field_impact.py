"""Tests for :func:`data_quality.analyze_field_impacts`."""

import copy
import unittest

from data_quality import ImpactInputError, analyze_field_impacts


def ref(dataset, field):
    return {"dataset": dataset, "field": field}


def edge(sd, sf, td, tf):
    return {
        "sourceDataset": sd,
        "sourceField": sf,
        "targetDataset": td,
        "targetField": tf,
    }


def rule(rule_id, status, fields, failed_ids):
    return {
        "ruleId": rule_id,
        "status": status,
        "fields": fields,
        "failedSampleIds": failed_ids,
    }


def sample(dataset, field_values):
    return {"dataset": dataset, "fieldValues": field_values}


# ods.name -> dwd.label -> ads.label; dwd.id is isolated.
BASE = {
    "datasets": {
        "ods": ["id", "name"],
        "dwd": ["id", "label"],
        "ads": ["label"],
    },
    "lineageEdges": [
        edge("ods", "name", "dwd", "label"),
        edge("dwd", "label", "ads", "label"),
    ],
    "validationResults": [
        rule(
            "r-fail",
            "failed",
            [ref("dwd", "label"), ref("ods", "name")],
            ["s1", "s2", "ghost"],
        ),
        rule("r-pass", "passed", [ref("ads", "label")], ["s3"]),
        rule("r-skip", "skipped", [ref("ads", "label")], ["s4"]),
        rule("r-isolated", "failed", [ref("dwd", "id")], ["s5"]),
    ],
    "anomalySamples": {
        "s1": sample("dwd", {"label": "x"}),
        "s2": sample("ads", {"label": "y"}),
        "s4": sample("ads", {"label": "z"}),
        "s5": sample("dwd", {"id": 0}),
    },
    "seedFields": [ref("ods", "name")],
}


class AnalyzeFieldImpactsSuccessTest(unittest.TestCase):
    def test_basic_impact(self):
        result = analyze_field_impacts(copy.deepcopy(BASE))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["unresolvedReferences"], [])
        self.assertEqual(len(result["impacts"]), 1)
        impact = result["impacts"][0]

        self.assertEqual(impact["seed"], ref("ods", "name"))
        # Reachability requires at least one edge; the seed is not a
        # downstream field of itself absent a self-loop/cycle.
        self.assertEqual(
            impact["downstreamFields"],
            [ref("ads", "label"), ref("dwd", "label")],
        )
        # passed rules and rules on unreachable fields are excluded; the
        # skipped rule touching ads.label counts.
        self.assertEqual(impact["affectedRules"], ["r-fail", "r-skip"])
        # Only ids present in anomalySamples survive; "ghost" and s3 (only
        # on the passed rule) do not; s5 belongs to an unrelated rule.
        self.assertEqual(impact["anomalySamples"], ["s1", "s2", "s4"])
        # One shortest reference sequence per downstream field, ordered by
        # full name: ads.label before dwd.label.
        self.assertEqual(
            impact["paths"],
            [
                [ref("ods", "name"), ref("dwd", "label"), ref("ads", "label")],
                [ref("ods", "name"), ref("dwd", "label")],
            ],
        )

    def test_input_is_not_mutated(self):
        payload = copy.deepcopy(BASE)
        analyze_field_impacts(payload)
        self.assertEqual(payload, BASE)

    def test_seed_order_preserved_with_duplicates(self):
        payload = copy.deepcopy(BASE)
        payload["seedFields"] = [
            ref("ods", "name"),
            ref("dwd", "id"),
            ref("ods", "name"),
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(
            [impact["seed"] for impact in result["impacts"]],
            [ref("ods", "name"), ref("dwd", "id"), ref("ods", "name")],
        )
        # Identical seeds produce identical impacts.
        self.assertEqual(result["impacts"][0], result["impacts"][2])
        # The isolated seed has no downstream fields at all.
        self.assertEqual(result["impacts"][1]["downstreamFields"], [])
        self.assertEqual(result["impacts"][1]["affectedRules"], [])
        self.assertEqual(result["impacts"][1]["anomalySamples"], [])
        self.assertEqual(result["impacts"][1]["paths"], [])

    def test_unknown_seed_is_empty(self):
        payload = copy.deepcopy(BASE)
        payload["seedFields"] = [
            ref("ghost", "field"),
            ref("ods", "missing"),
            ref("ods", "name"),
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(result["impacts"][0]["downstreamFields"], [])
        self.assertEqual(result["impacts"][1]["downstreamFields"], [])
        self.assertEqual(
            result["impacts"][2]["downstreamFields"],
            [ref("ads", "label"), ref("dwd", "label")],
        )

    def test_empty_seed_fields_keeps_dangling_edges(self):
        payload = copy.deepcopy(BASE)
        payload["seedFields"] = []
        payload["lineageEdges"].append(edge("nope", "x", "ods", "name"))
        result = analyze_field_impacts(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["impacts"], [])
        self.assertEqual(
            result["unresolvedReferences"],
            [edge("nope", "x", "ods", "name")],
        )

    def test_empty_everything(self):
        payload = {
            "datasets": {},
            "lineageEdges": [],
            "validationResults": [],
            "anomalySamples": {},
            "seedFields": [],
        }
        self.assertEqual(
            analyze_field_impacts(payload),
            {"status": "ok", "impacts": [], "unresolvedReferences": []},
        )


class SelfLoopAndCycleTest(unittest.TestCase):
    def _payload(
        self, edges, datasets=None, rules=None, samples=None, seeds=None
    ):
        return {
            "datasets": datasets
            if datasets is not None
            else {"a": ["x"], "b": ["y"]},
            "lineageEdges": edges,
            "validationResults": rules
            if rules is not None
            else [
                rule(
                    "r1",
                    "failed",
                    [ref("a", "x"), ref("b", "y")],
                    ["s1"],
                )
            ],
            "anomalySamples": samples
            if samples is not None
            else {"s1": sample("a", {})},
            "seedFields": seeds
            if seeds is not None
            else [ref("a", "x")],
        }

    def test_self_loop_makes_seed_reachable(self):
        result = analyze_field_impacts(
            self._payload([edge("a", "x", "a", "x")])
        )
        impact = result["impacts"][0]
        self.assertEqual(impact["downstreamFields"], [ref("a", "x")])
        self.assertEqual(impact["affectedRules"], ["r1"])
        self.assertEqual(impact["anomalySamples"], ["s1"])
        self.assertEqual(
            impact["paths"], [[ref("a", "x"), ref("a", "x")]]
        )

    def test_two_cycle_back_to_seed(self):
        result = analyze_field_impacts(
            self._payload(
                [
                    edge("a", "x", "b", "y"),
                    edge("b", "y", "a", "x"),
                ]
            )
        )
        impact = result["impacts"][0]
        self.assertEqual(
            impact["downstreamFields"], [ref("a", "x"), ref("b", "y")]
        )
        normalized = sorted(
            tuple((n["dataset"], n["field"]) for n in path)
            for path in impact["paths"]
        )
        self.assertEqual(
            normalized,
            [
                (("a", "x"), ("b", "y")),
                (("a", "x"), ("b", "y"), ("a", "x")),
            ],
        )

    def test_shortest_cycle_back_to_seed_wins(self):
        # A two-edge cycle via m.u and a three-edge cycle via q.v/r.w.
        datasets = {"s": ["x"], "m": ["u"], "q": ["v"], "r": ["w"]}
        edges = [
            edge("s", "x", "m", "u"),
            edge("m", "u", "s", "x"),
            edge("s", "x", "q", "v"),
            edge("q", "v", "r", "w"),
            edge("r", "w", "s", "x"),
        ]
        payload = self._payload(
            edges,
            datasets=datasets,
            rules=[],
            samples={},
            seeds=[ref("s", "x")],
        )
        result = analyze_field_impacts(payload)
        seed_paths = [
            path
            for path in result["impacts"][0]["paths"]
            if path[-1] == ref("s", "x")
        ]
        self.assertEqual(
            seed_paths,
            [[ref("s", "x"), ref("m", "u"), ref("s", "x")]],
        )


class ShortestPathSelectionTest(unittest.TestCase):
    def test_diamond_picks_shortest_then_lex_smallest(self):
        # Two equal-length chains to T.t; via A.a is lex-smaller than Z.z.
        payload = {
            "datasets": {
                "S": ["s"], "A": ["a"], "Z": ["z"], "T": ["t"],
            },
            "lineageEdges": [
                edge("S", "s", "Z", "z"),
                edge("S", "s", "A", "a"),
                edge("Z", "z", "T", "t"),
                edge("A", "a", "T", "t"),
                # A direct edge is shorter and must win regardless of names.
                edge("S", "s", "T", "t"),
            ],
            "validationResults": [],
            "anomalySamples": {},
            "seedFields": [ref("S", "s")],
        }
        result = analyze_field_impacts(payload)
        paths = {
            path[-1]["dataset"] + "." + path[-1]["field"]: path
            for path in result["impacts"][0]["paths"]
        }
        self.assertEqual(paths["T.t"], [ref("S", "s"), ref("T", "t")])
        self.assertEqual(
            paths["A.a"], [ref("S", "s"), ref("A", "a")]
        )
        self.assertEqual(
            paths["Z.z"], [ref("S", "s"), ref("Z", "z")]
        )

    def test_diamond_lex_tie_without_direct_edge(self):
        payload = {
            "datasets": {
                "S": ["s"], "A": ["a"], "Z": ["z"], "T": ["t"],
            },
            "lineageEdges": [
                edge("S", "s", "Z", "z"),
                edge("S", "s", "A", "a"),
                edge("Z", "z", "T", "t"),
                edge("A", "a", "T", "t"),
            ],
            "validationResults": [],
            "anomalySamples": {},
            "seedFields": [ref("S", "s")],
        }
        result = analyze_field_impacts(payload)
        paths = result["impacts"][0]["paths"]
        t_path = [path for path in paths if path[-1] == ref("T", "t")][0]
        self.assertEqual(
            t_path, [ref("S", "s"), ref("A", "a"), ref("T", "t")]
        )

    def test_lex_tie_uses_full_name_not_dataset_then_field(self):
        # As (dataset, field) tuples, ("a", "z") sorts before ("a-z", "a"),
        # but as full names "a-z.a" sorts before "a.z" ('-' < '.'). The
        # reference sequence order is defined over full names.
        payload = {
            "datasets": {
                "D": ["f"], "a": ["z"], "a-z": ["a"], "T": ["t"],
            },
            "lineageEdges": [
                edge("D", "f", "a", "z"),
                edge("D", "f", "a-z", "a"),
                edge("a", "z", "T", "t"),
                edge("a-z", "a", "T", "t"),
            ],
            "validationResults": [],
            "anomalySamples": {},
            "seedFields": [ref("D", "f")],
        }
        result = analyze_field_impacts(payload)
        paths = result["impacts"][0]["paths"]
        t_path = [path for path in paths if path[-1] == ref("T", "t")][0]
        self.assertEqual(
            t_path, [ref("D", "f"), ref("a-z", "a"), ref("T", "t")]
        )


class DanglingEdgeTest(unittest.TestCase):
    def test_dangling_edges_sorted_by_four_keys_and_skipped(self):
        payload = copy.deepcopy(BASE)
        payload["lineageEdges"] = [
            edge("z", "1", "ods", "name"),
            edge("dwd", "label", "z", "9"),
            edge("a", "0", "dwd", "label"),
            edge("dwd", "label", "a", "2"),
            # duplicate dangling edge is legal and deduplicated
            edge("a", "0", "dwd", "label"),
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(
            result["unresolvedReferences"],
            [
                edge("a", "0", "dwd", "label"),
                edge("dwd", "label", "a", "2"),
                edge("dwd", "label", "z", "9"),
                edge("z", "1", "ods", "name"),
            ],
        )
        # Traversal ignores dangling edges: ods.name still reaches the
        # declared chain even though other edges are unresolved.
        payload2 = copy.deepcopy(BASE)
        payload2["lineageEdges"].append(edge("z", "q", "ads", "label"))
        result2 = analyze_field_impacts(payload2)
        self.assertEqual(
            result2["impacts"][0]["downstreamFields"],
            [ref("ads", "label"), ref("dwd", "label")],
        )
        self.assertEqual(len(result2["unresolvedReferences"]), 1)

    def test_duplicate_resolved_edges_deduplicated(self):
        payload = copy.deepcopy(BASE)
        payload["lineageEdges"].append(edge("ods", "name", "dwd", "label"))
        result = analyze_field_impacts(payload)
        self.assertEqual(result["unresolvedReferences"], [])
        self.assertEqual(
            result["impacts"][0]["downstreamFields"],
            [ref("ads", "label"), ref("dwd", "label")],
        )


class SampleLocationTest(unittest.TestCase):
    def test_only_located_failed_samples_appear(self):
        payload = copy.deepcopy(BASE)
        payload["validationResults"] = [
            rule(
                "r",
                "failed",
                [ref("dwd", "label")],
                ["present", "missing", "present"],
            ),
        ]
        payload["anomalySamples"] = {
            "present": sample("wherever", {"f": None}),
        }
        result = analyze_field_impacts(payload)
        # Located ids are deduplicated; the missing id is dropped.
        self.assertEqual(
            result["impacts"][0]["anomalySamples"], ["present"]
        )

    def test_duplicate_rule_id_is_deduplicated(self):
        payload = copy.deepcopy(BASE)
        payload["validationResults"] = [
            rule("dup", "failed", [ref("dwd", "label")], ["s1"]),
            rule("dup", "failed", [ref("ads", "label")], ["s1", "s2"]),
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(
            result["impacts"][0]["affectedRules"], ["dup"]
        )
        self.assertEqual(
            result["impacts"][0]["anomalySamples"], ["s1", "s2"]
        )

    def test_duplicate_rule_id_samples_only_from_qualifying_rules(self):
        # The id "dup" is shared by a qualifying failed rule and a passed
        # rule; only the failed rule's locatable samples are reported.
        payload = copy.deepcopy(BASE)
        payload["validationResults"] = [
            rule("dup", "failed", [ref("dwd", "label")], ["s1"]),
            rule("dup", "passed", [ref("ads", "label")], ["s4"]),
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(
            result["impacts"][0]["affectedRules"], ["dup"]
        )
        self.assertEqual(
            result["impacts"][0]["anomalySamples"], ["s1"]
        )


class AnalyzeFieldImpactsErrorTest(unittest.TestCase):
    def _assert_error(self, payload):
        with self.assertRaises(ImpactInputError):
            analyze_field_impacts(copy.deepcopy(payload))

    def test_payload_not_object(self):
        for bad in (None, [], "x", 1, True):
            with self.assertRaises(ImpactInputError):
                analyze_field_impacts(bad)

    def test_top_level_missing_and_extra_keys(self):
        for key in (
            "datasets",
            "lineageEdges",
            "validationResults",
            "anomalySamples",
            "seedFields",
        ):
            bad = copy.deepcopy(BASE)
            del bad[key]
            self._assert_error(bad)
        bad = copy.deepcopy(BASE)
        bad["extra"] = 1
        self._assert_error(bad)

    def test_datasets_structure(self):
        bad = copy.deepcopy(BASE)
        bad["datasets"] = []
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["datasets"] = {"": ["x"]}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["datasets"] = {"ods": "name"}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["datasets"] = {"ods": ["name", ""]}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["datasets"] = {"ods": [1]}
        self._assert_error(bad)

    def test_edges_structure(self):
        bad = copy.deepcopy(BASE)
        bad["lineageEdges"] = {}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["lineageEdges"] = [edge("ods", "name", "dwd", "label")]
        bad["lineageEdges"][0]["extra"] = 1
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["lineageEdges"] = [
            {"sourceDataset": "ods", "sourceField": "name",
             "targetDataset": "dwd"}
        ]
        self._assert_error(bad)

        for key in (
            "sourceDataset", "sourceField", "targetDataset", "targetField"
        ):
            bad = copy.deepcopy(BASE)
            e = edge("ods", "name", "dwd", "label")
            e[key] = ""
            bad["lineageEdges"] = [e]
            self._assert_error(bad)

            bad = copy.deepcopy(BASE)
            e = edge("ods", "name", "dwd", "label")
            e[key] = 3
            bad["lineageEdges"] = [e]
            self._assert_error(bad)

    def test_rules_structure(self):
        bad = copy.deepcopy(BASE)
        bad["validationResults"] = {}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["ruleId"] = ""
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["ruleId"] = 7
        self._assert_error(bad)

        for bad_status in ("PASSED", "warning", "", None, 1):
            bad = copy.deepcopy(BASE)
            bad["validationResults"][0]["status"] = bad_status
            self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["extra"] = 1
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        del bad["validationResults"][0]["status"]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["fields"] = {}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["fields"] = [{"dataset": "dwd"}]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["fields"] = [
            {"dataset": "", "field": "label"}
        ]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["fields"] = [
            {"dataset": "dwd", "field": None}
        ]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["failedSampleIds"] = "s1"
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["failedSampleIds"] = [""]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["validationResults"][0]["failedSampleIds"] = [1]
        self._assert_error(bad)

    def test_rule_field_reference_to_undeclared_field_is_allowed(self):
        # Structural validity only; an undeclared rule field simply never
        # intersects a downstream set.
        payload = copy.deepcopy(BASE)
        payload["validationResults"] = [
            rule("r", "failed", [ref("ghost", "x")], ["s1"])
        ]
        result = analyze_field_impacts(payload)
        self.assertEqual(result["impacts"][0]["affectedRules"], [])

    def test_samples_structure(self):
        bad = copy.deepcopy(BASE)
        bad["anomalySamples"] = []
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"] = {"": sample("a", {})}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {"dataset": "dwd"}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {
            "dataset": "dwd", "fieldValues": {}, "extra": 1
        }
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {
            "dataset": "", "fieldValues": {}
        }
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {
            "dataset": "dwd", "fieldValues": []
        }
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["anomalySamples"]["s1"] = {
            "dataset": "dwd", "fieldValues": {"": 1}
        }
        self._assert_error(bad)

    def test_seeds_structure(self):
        bad = copy.deepcopy(BASE)
        bad["seedFields"] = {}
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = [{"dataset": "ods"}]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = [{"dataset": "ods", "field": "name", "x": 1}]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = [{"dataset": None, "field": "name"}]
        self._assert_error(bad)

        bad = copy.deepcopy(BASE)
        bad["seedFields"] = ["ods.name"]
        self._assert_error(bad)

    def test_error_is_value_error(self):
        self.assertTrue(issubclass(ImpactInputError, ValueError))
        bad = copy.deepcopy(BASE)
        bad["seedFields"] = None
        with self.assertRaises(ValueError):
            analyze_field_impacts(bad)


if __name__ == "__main__":
    unittest.main()
