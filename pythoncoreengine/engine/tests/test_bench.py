"""Unit tests for the Zerodha benchmark harness (engine.bench_zerodha).

Covers the 80/15/5 schedule determinism, the cache-equivalence guarantee
(cached and uncached runs emit byte-identical documents for the same
seed), the non-compliant mode (bytes differ, no /StructTreeRoot, no
/OutputIntents), warm-up PDF writing (both flavours, deterministic bytes)
and the worker-clamp helper.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from engine.bench_zerodha import (
    _clamp_workers,
    _expanded_models,
    _load_base,
    _repo_root,
    plan_jobs,
    run_bench,
    write_warmup_pdfs,
)
from engine.model import load_json
from engine.render import build_document
from engine.tests.helpers import find_object_with, object_bytes, parse_xref

DATA_DIR = _repo_root() / "sampledata" / "zerodha"


class TestPlanJobs(unittest.TestCase):
    def test_tier_mix_80_15_5(self) -> None:
        jobs = plan_jobs(2000, seed=42)
        self.assertEqual(len(jobs), 2000)
        self.assertGreater(jobs.count("retail"), 1500)
        self.assertGreater(jobs.count("active"), 250)
        self.assertGreater(jobs.count("hft"), 80)

    def test_plan_deterministic_per_seed(self) -> None:
        self.assertEqual(plan_jobs(100, 7), plan_jobs(100, 7))

    def test_plan_changes_with_seed(self) -> None:
        self.assertNotEqual(plan_jobs(100, 7), plan_jobs(100, 8))


class TestBenchCacheEquivalence(unittest.TestCase):
    def test_cached_and_uncached_identical_bytes(self) -> None:
        cached = run_bench(20, seed=42, cached=True, data_dir=DATA_DIR)
        uncached = run_bench(20, seed=42, cached=False, data_dir=DATA_DIR)
        self.assertEqual(cached["md5"], uncached["md5"])
        self.assertEqual(
            sum(tier["jobs"] for tier in cached["tiers"].values()), 20
        )
        self.assertGreater(cached["jobs_per_sec"], 0.0)


class TestBenchNonCompliant(unittest.TestCase):
    def test_nocomply_differs_from_compliant(self) -> None:
        compliant = run_bench(12, seed=42, cached=True, compliant=True, data_dir=DATA_DIR)
        plain = run_bench(12, seed=42, cached=True, compliant=False, data_dir=DATA_DIR)
        self.assertNotEqual(compliant["md5"], plain["md5"])
        note = load_json(DATA_DIR / "retail_investor.json")
        data = build_document(note, compliant=False)
        offsets = parse_xref(data)
        catalog = object_bytes(data, offsets[find_object_with(data, b"/Type /Catalog", offsets)])
        self.assertNotIn(b"/StructTreeRoot", catalog)
        self.assertNotIn(b"/OutputIntents", catalog)


class TestWarmupWrites(unittest.TestCase):
    def test_warmup_pdfs_written(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        models = {"retail": note, "active": note, "hft": note}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            paths = write_warmup_pdfs(models, out, compliant=True)
            for tier in ("retail", "active", "hft"):
                self.assertTrue(paths[tier].is_file())
                self.assertGreater(paths[tier].stat().st_size, 1000)
            nocomply = write_warmup_pdfs(models, out, compliant=False)
            self.assertTrue((out / "zerodha_retail_nocomply_output.pdf").is_file())
            self.assertNotEqual(
                nocomply["retail"].read_bytes(), paths["retail"].read_bytes()
            )

    def test_warmup_bytes_deterministic(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        models = {"retail": note, "active": note, "hft": note}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = write_warmup_pdfs(models, out)["retail"].read_bytes()
            second = write_warmup_pdfs(models, out)["retail"].read_bytes()
            self.assertEqual(first, second)
            self.assertEqual(
                hashlib.md5(first).hexdigest(), hashlib.md5(second).hexdigest()
            )


class TestExpandedModels(unittest.TestCase):
    def test_models_use_target_counts(self) -> None:
        base = _load_base(DATA_DIR)
        models = _expanded_models(base, seed=42)
        self.assertEqual(len(models["retail"].trades), 2)
        self.assertEqual(len(models["active"].trades), 40)
        self.assertEqual(len(models["hft"].trades), 2000)


class TestWorkersClamp(unittest.TestCase):
    def test_clamp_bounds(self) -> None:
        self.assertEqual(_clamp_workers(1), 1)
        self.assertEqual(_clamp_workers(10 ** 9), (os.cpu_count() or 1) * 2)
        self.assertGreaterEqual(_clamp_workers(48), 2)


if __name__ == "__main__":
    unittest.main()
