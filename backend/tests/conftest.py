"""Shared fixtures. Imports of fastapi/backend code happen inside the fixtures so that a
machine without the backend dependencies can still collect the rest of the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from . import fixture_data as fx

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo_data"


@pytest.fixture(scope="session")
def processed_dir(tmp_path_factory) -> Path:
    """14 works, 11 anomaly records, all four CSVs."""
    return fx.build_processed_dir(tmp_path_factory.mktemp("processed"))


@pytest.fixture(scope="session")
def settings(processed_dir):
    from backend.app.config import Settings

    return Settings(processed_dir=processed_dir, demo_dir=DEMO_DIR)


@pytest.fixture(scope="session")
def client(settings):
    """Read-only client shared by the tests that never touch the data files."""
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    return TestClient(create_app(settings))
