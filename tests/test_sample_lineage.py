"""Tests for :func:`data_quality.trace_sample_lineage`."""

import copy
import unittest

from data_quality import (
    InvalidSampleLineageInputError,
    InvalidSampleLineageQueryError,
    UnknownSampleLineageReferenceError,
    UnknownSampleLineageTargetError,
    trace_sample_lineage,
)


def sample(dataset, key):
    return {"dataset_id": dataset, "sample_id": key}


def field(table, column):
    return {"table": table, "column": column}


def witness(dataset, key, table, column):
    return {
        "dataset_id": dataset,
        "sample_id": key,
        "table": table,
        "column": column,
    }


def edge(source, target, witnesses):
    return {"source": source, "target": target, "witnesses": witnesses}


def link(left, right):
    return {"left": left, "right": right}


SAMPLES = [
    sample("raw", "r"),
    sample("ods", "a"),
    sample("ods", "a2"),
    sample("dwd", "b"),
    sample("ads", "c"),
    sample("ads", "z"),
]

FIELDS = {
    "raw": ["name"],
    "ods": ["name"],
    "dwd": ["label"],
    "ads": ["label"],
}

EDGES = [
    edge(
        field("raw", "name"),
        field("ods", "name"),
        [
            witness("raw", "r", "raw", "name"),
            witness("ods", "a", "ods", "name"),
        ],
    ),
    edge(
        field("ods", "name"),
        field("dwd", "label"),
        [
            witness("ods", "a", "ods", "name"),
            witness("dwd", "b", "dwd", "label"),
        ],
    ),
    edge(
        field("dwd", "label"),
        field("ads", "label"),
        [
            witness("dwd", "b", "dwd", "label"),
            witness("ads", "c", "ads", "label"),
        ],
    ),
]

# Self-loops are legal; this one connects no two distinct samples. It is
# added only in dedicated tests because it is always reached from b.
SELF_LOOP_EDGE = edge(
    field("dwd", "label"),
    field("dwd", "label"),
    [witness("dwd", "b", "dwd", "label")],
)

SAMPLE_LINKS = [
    link(sample("ads", "c"), sample("ads", "z")),
    link(sample("dwd", "b"), sample("ods", "a2")),
]

TARGET = sample("dwd", "b")


def trace(**overrides):
    kwargs = dict(
        samples=SAMPLES,
        fields=FIELDS,
        edges=EDGES,
        sample_links=SAMPLE_LINKS,
        target=TARGET,
    )
    kwargs.update(overrides)
    return trace_sample_lineage(**kwargs)


class TraceSampleLineageSuccessTest(unittest.TestCase):
    def test_both_directions_unlimited(self):
        result = trace()
        self.assertEqual(result["target"], TARGET)
        self.assertEqual(result["direction"], "both")
        self.assertIsNone(result["max_depth"])

        self.assertEqual(
            result["upstream"]["samples"],
            [
                {**sample("dwd", "b"), "depth": 0},
                {**sample("ods", "a"), "depth": 1},
                {**sample("ods", "a2"), "depth": 1},
                {**sample("raw", "r"), "depth": 2},
            ],
        )
        self.assertEqual(
            result["downstream"]["samples"],
            [
                {**sample("dwd", "b"), "depth": 0},
                {**sample("ads", "c"), "depth": 1},
                {**sample("ods", "a2"), "depth": 1},
                {**sample("ads", "z"), "depth": 2},
            ],
        )

    def test_edges_sorted_by_sample_then_field_references(self):
        result = trace()
        # Edges sort by witness sample refs then field refs: ods->dwd
        # (dwd/b, ods/a) precedes raw->ods (ods/a, raw/r).
        self.assertEqual(
            [
                (e["source"], e["target"])
                for e in result["upstream"]["edges"]
            ],
            [
                (field("ods", "name"), field("dwd", "label")),
                (field("raw", "name"), field("ods", "name")),
            ],
        )
        self.assertEqual(
            [
                (e["source"], e["target"])
                for e in result["downstream"]["edges"]
            ],
            [(field("dwd", "label"), field("ads", "label"))],
        )

    def test_self_loop_edge_is_kept_when_witnessed_by_target(self):
        result = trace(edges=EDGES + [SELF_LOOP_EDGE])
        self.assertEqual(
            [
                (e["source"], e["target"])
                for e in result["upstream"]["edges"]
            ],
            [
                (field("dwd", "label"), field("dwd", "label")),
                (field("ods", "name"), field("dwd", "label")),
                (field("raw", "name"), field("ods", "name")),
            ],
        )
        self_loop = result["upstream"]["edges"][0]
        self.assertEqual(
            self_loop["witnesses"],
            [witness("dwd", "b", "dwd", "label")],
        )
        # The self-loop never reaches another sample.
        self.assertNotIn(
            {**sample("dwd", "b"), "depth": 1},
            result["upstream"]["samples"],
        )

    def test_witnesses_sorted_by_field_then_sample_reference(self):
        result = trace()
        ods_dwd = result["upstream"]["edges"][0]
        self.assertEqual(
            ods_dwd["witnesses"],
            [
                witness("dwd", "b", "dwd", "label"),
                witness("ods", "a", "ods", "name"),
            ],
        )
        raw_ods = result["upstream"]["edges"][1]
        self.assertEqual(
            raw_ods["witnesses"],
            [
                witness("ods", "a", "ods", "name"),
                witness("raw", "r", "raw", "name"),
            ],
        )

    def test_sample_links_are_undirected_on_both_sides(self):
        # From ads/c upstream the undirected link reaches ads/z and from
        # ods/a2 the link reaches dwd/b.
        result = trace(target=sample("ads", "c"))
        upstream_ids = [
            (s["dataset_id"], s["sample_id"])
            for s in result["upstream"]["samples"]
        ]
        self.assertIn(("ads", "z"), upstream_ids)
        self.assertEqual(
            result["upstream"]["samples"][0],
            {**sample("ads", "c"), "depth": 0},
        )
        # ads/z is one undirected hop away even on the upstream side.
        z = next(s for s in result["upstream"]["samples"]
                 if s["sample_id"] == "z")
        self.assertEqual(z["depth"], 1)

    def test_unqueried_side_is_empty(self):
        result = trace(direction="upstream")
        self.assertEqual(result["direction"], "upstream")
        self.assertEqual(
            result["downstream"], {"samples": [], "edges": []}
        )
        self.assertGreater(len(result["upstream"]["samples"]), 1)

        result = trace(direction="downstream")
        self.assertEqual(result["upstream"], {"samples": [], "edges": []})
        self.assertGreater(len(result["downstream"]["samples"]), 1)

    def test_max_depth_zero_returns_only_target(self):
        result = trace(max_depth=0)
        self.assertEqual(result["max_depth"], 0)
        for side in ("upstream", "downstream"):
            self.assertEqual(
                result[side]["samples"],
                [{**TARGET, "depth": 0}],
            )
            self.assertEqual(result[side]["edges"], [])

    def test_max_depth_limits_hops_and_edge_inclusion(self):
        result = trace(direction="downstream", max_depth=1)
        ids = {
            (s["dataset_id"], s["sample_id"]): s["depth"]
            for s in result["downstream"]["samples"]
        }
        # Undirected sample links count on the downstream side too.
        self.assertEqual(
            ids,
            {("dwd", "b"): 0, ("ads", "c"): 1, ("ods", "a2"): 1},
        )
        # ads/z is unreachable at depth 1; the dwd->ads edge stays because
        # both its witness samples reached.
        self.assertEqual(
            [
                (e["source"], e["target"])
                for e in result["downstream"]["edges"]
            ],
            [(field("dwd", "label"), field("ads", "label"))],
        )

        result = trace(direction="upstream", max_depth=1)
        ids = {
            (s["dataset_id"], s["sample_id"]): s["depth"]
            for s in result["upstream"]["samples"]
        }
        self.assertEqual(
            ids,
            {("dwd", "b"): 0, ("ods", "a"): 1, ("ods", "a2"): 1},
        )
        # raw/r at depth 2 is beyond the bound: the raw->ods edge loses
        # its source-side witness and is excluded.
        self.assertEqual(
            [
                (e["source"], e["target"])
                for e in result["upstream"]["edges"]
            ],
            [(field("ods", "name"), field("dwd", "label"))],
        )

    def test_shortest_depth_wins_with_cycles(self):
        edges = EDGES + [
            edge(
                field("ads", "label"),
                field("dwd", "label"),
                [
                    witness("ads", "c", "ads", "label"),
                    witness("dwd", "b", "dwd", "label"),
                ],
            )
        ]
        result = trace(edges=edges, target=sample("ads", "z"))
        upstream = result["upstream"]["samples"]
        depths = {
            (s["dataset_id"], s["sample_id"]): s["depth"]
            for s in upstream
        }
        # z -> c via link (1), c -> b via edge (2); cycles never inflate
        # the shortest depth.
        self.assertEqual(depths[("ads", "z")], 0)
        self.assertEqual(depths[("ads", "c")], 1)
        self.assertEqual(depths[("dwd", "b")], 2)

    def test_isolated_target_has_only_itself(self):
        result = trace(
            samples=[sample("ods", "a"), sample("dwd", "q")],
            fields={"ods": ["name"], "dwd": ["label"]},
            edges=[],
            sample_links=[],
            target=sample("dwd", "q"),
        )
        for side in ("upstream", "downstream"):
            self.assertEqual(
                result[side]["samples"],
                [{**sample("dwd", "q"), "depth": 0}],
            )
            self.assertEqual(result[side]["edges"], [])

    def test_input_is_not_modified(self):
        snapshots = [
            copy.deepcopy(SAMPLES),
            copy.deepcopy(FIELDS),
            copy.deepcopy(EDGES),
            copy.deepcopy(SAMPLE_LINKS),
            copy.deepcopy(TARGET),
        ]
        trace()
        self.assertEqual(snapshots[0], SAMPLES)
        self.assertEqual(snapshots[1], FIELDS)
        self.assertEqual(snapshots[2], EDGES)
        self.assertEqual(snapshots[3], SAMPLE_LINKS)
        self.assertEqual(snapshots[4], TARGET)


class TraceSampleLineageInputErrorTest(unittest.TestCase):
    def assertInvalid(self, **overrides):
        with self.assertRaises(InvalidSampleLineageInputError):
            trace(**overrides)

    def test_samples_malformed(self):
        self.assertInvalid(samples="nope")
        self.assertInvalid(samples=[sample("ods", "a"), "nope"])
        self.assertInvalid(samples=[{"dataset_id": "ods"}])
        self.assertInvalid(samples=[{"dataset_id": "ods", "sample_id": "a",
                                    "extra": 1}])
        self.assertInvalid(samples=[{"dataset_id": "", "sample_id": "a"}])
        self.assertInvalid(samples=[{"dataset_id": "ods", "sample_id": ""}])
        self.assertInvalid(
            samples=[sample("ods", "a"), sample("ods", "a")]
        )

    def test_fields_malformed(self):
        self.assertInvalid(fields="nope")
        self.assertInvalid(fields={"ods": "name"})
        self.assertInvalid(fields={"": ["name"]})
        self.assertInvalid(fields={"ods": [""]})
        self.assertInvalid(fields={"ods": ["name", "name"]})

    def test_edges_malformed(self):
        good_edges = [
            edge(
                field("ods", "name"),
                field("dwd", "label"),
                [
                    witness("ods", "a", "ods", "name"),
                    witness("dwd", "b", "dwd", "label"),
                ],
            )
        ]
        self.assertInvalid(edges="nope")
        self.assertInvalid(edges=[{"source": field("ods", "name"),
                                   "target": field("dwd", "label")}])
        self.assertInvalid(
            edges=[
                {
                    "source": field("ods", "name"),
                    "target": field("dwd", "label"),
                    "witnesses": [],
                    "extra": 1,
                }
            ]
        )
        self.assertInvalid(
            edges=[
                edge(
                    field("ods", "nope"),
                    field("dwd", "label"),
                    [
                        witness("ods", "a", "ods", "nope"),
                        witness("dwd", "b", "dwd", "label"),
                    ],
                )
            ]
        )
        self.assertInvalid(edges=good_edges + good_edges)

    def test_witnesses_malformed(self):
        def edge_with(witnesses):
            return [
                edge(field("ods", "name"), field("dwd", "label"), witnesses)
            ]

        self.assertInvalid(edges=edge_with("nope"))
        self.assertInvalid(
            edges=edge_with([{"dataset_id": "ods", "sample_id": "a",
                              "table": "ods"}])
        )
        self.assertInvalid(
            edges=edge_with(
                [
                    {"dataset_id": "ods", "sample_id": "a",
                     "table": "ods", "column": "name", "extra": 1},
                    witness("dwd", "b", "dwd", "label"),
                ]
            )
        )
        self.assertInvalid(
            edges=edge_with(
                [
                    witness("ods", "", "ods", "name"),
                    witness("dwd", "b", "dwd", "label"),
                ]
            )
        )
        # Unknown witness sample is a structural error.
        self.assertInvalid(
            edges=edge_with(
                [
                    witness("ods", "ghost", "ods", "name"),
                    witness("dwd", "b", "dwd", "label"),
                ]
            )
        )
        # Unknown witness field is a structural error.
        self.assertInvalid(
            edges=edge_with(
                [
                    witness("ods", "a", "ods", "ghost"),
                    witness("dwd", "b", "dwd", "label"),
                ]
            )
        )
        # ads.label is declared but is neither edge endpoint.
        self.assertInvalid(
            edges=edge_with(
                [
                    witness("ads", "c", "ads", "label"),
                    witness("dwd", "b", "dwd", "label"),
                ]
            )
        )
        self.assertInvalid(
            edges=edge_with(
                [
                    witness("ods", "a", "ods", "name"),
                    witness("dwd", "b", "dwd", "label"),
                    witness("ods", "a", "ods", "name"),
                ]
            )
        )

    def test_sample_links_malformed(self):
        self.assertInvalid(sample_links="nope")
        self.assertInvalid(
            sample_links=[{"left": sample("ads", "c")}]
        )
        self.assertInvalid(
            sample_links=[
                {"left": sample("ads", "c"),
                 "right": sample("ads", "c"), "extra": 1}
            ]
        )
        self.assertInvalid(
            sample_links=[
                link(sample("ads", "c"), sample("ads", "c"))
            ]
        )
        good = link(sample("ads", "c"), sample("ads", "z"))
        self.assertInvalid(sample_links=[good, good])
        # Undirected: reversal is the same relationship.
        self.assertInvalid(
            sample_links=[
                good,
                link(sample("ads", "z"), sample("ads", "c")),
            ]
        )
        self.assertInvalid(
            sample_links=[
                link({"dataset_id": "ads", "sample_id": ""},
                     sample("ads", "z"))
            ]
        )


class TraceSampleLineageQueryErrorTest(unittest.TestCase):
    def assertQueryError(self, **overrides):
        with self.assertRaises(InvalidSampleLineageQueryError):
            trace(**overrides)

    def test_target_shape(self):
        self.assertQueryError(target="nope")
        self.assertQueryError(target={"dataset_id": "dwd"})
        self.assertQueryError(
            target={"dataset_id": "dwd", "sample_id": "b", "extra": 1}
        )
        self.assertQueryError(
            target={"dataset_id": "", "sample_id": "b"}
        )
        self.assertQueryError(
            target={"dataset_id": "dwd", "sample_id": ""}
        )

    def test_direction(self):
        self.assertQueryError(direction="sideways")
        self.assertQueryError(direction=None)
        self.assertQueryError(direction=1)

    def test_max_depth(self):
        self.assertQueryError(max_depth=True)
        self.assertQueryError(max_depth=False)
        self.assertQueryError(max_depth=-1)
        self.assertQueryError(max_depth=1.5)
        self.assertQueryError(max_depth="1")


class TraceSampleLineageReferenceErrorTest(unittest.TestCase):
    def test_unknown_target(self):
        with self.assertRaises(UnknownSampleLineageTargetError):
            trace(target=sample("dwd", "ghost"))

    def test_unknown_sample_link_endpoint(self):
        with self.assertRaises(UnknownSampleLineageReferenceError):
            trace(
                sample_links=[
                    link(sample("dwd", "b"), sample("dwd", "ghost"))
                ]
            )

    def test_unknown_target_takes_precedence_over_unknown_endpoint(self):
        with self.assertRaises(UnknownSampleLineageTargetError):
            trace(
                target=sample("dwd", "ghost"),
                sample_links=[
                    link(sample("dwd", "b"), sample("dwd", "ghost2"))
                ],
            )

    def test_structural_error_takes_precedence_over_reference_errors(self):
        with self.assertRaises(InvalidSampleLineageInputError):
            trace(
                samples="nope",
                sample_links=[
                    link(sample("dwd", "b"), sample("dwd", "ghost"))
                ],
                target=sample("dwd", "ghost"),
            )
        with self.assertRaises(InvalidSampleLineageInputError):
            trace(
                sample_links=[
                    link(sample("dwd", "ghost3"), sample("dwd", "ghost3"))
                ],
                target=sample("dwd", "ghost"),
            )

    def test_exception_classes(self):
        self.assertTrue(issubclass(InvalidSampleLineageInputError, ValueError))
        self.assertTrue(issubclass(InvalidSampleLineageQueryError, ValueError))
        self.assertTrue(
            issubclass(UnknownSampleLineageTargetError, LookupError)
        )
        self.assertTrue(
            issubclass(UnknownSampleLineageReferenceError, LookupError)
        )


if __name__ == "__main__":
    unittest.main()
