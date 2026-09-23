"""Shared fixtures: synthetic raw workbooks and a finished pipeline run."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from helpers import build_raw_dir

from data_pipeline.build_dataset import PipelineConfig, run_pipeline

AS_OF = dt.date(2026, 9, 20)


@pytest.fixture
def raw_dir(tmp_path):
    return build_raw_dir(tmp_path / "raw")


@pytest.fixture
def pipeline(tmp_path, raw_dir):
    out = tmp_path / "out"
    stats = run_pipeline(
        PipelineConfig(raw_dir=raw_dir, output_root=out, as_of=AS_OF, demo_size=2, demo_seed="test-seed", progress_every=1000)
    )
    return SimpleNamespace(
        stats=stats,
        out=out,
        raw=raw_dir,
        processed=out / "data" / "processed",
        demo=out / "data" / "demo",
        docs=out / "docs",
    )
