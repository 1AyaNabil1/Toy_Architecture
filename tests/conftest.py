"""Shared pytest fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from helpers import SAMPLE_MODEL_DIR, write_zip


@pytest.fixture
def make_zip(tmp_path):
    counter = iter(range(1_000_000))

    def _make(entries, compression=zipfile.ZIP_DEFLATED, name=None) -> Path:
        path = tmp_path / (name or f"bundle{next(counter)}.zip")
        return write_zip(path, entries, compression)

    return _make


@pytest.fixture
def sample_zip(tmp_path) -> Path:
    """The example bundle from examples/sample_model, zipped as a folder."""
    entries = [
        (f"sample_model/{path.name}", path.read_bytes())
        for path in sorted(SAMPLE_MODEL_DIR.iterdir())
        if path.is_file() and not path.name.startswith(".")
    ]
    return write_zip(tmp_path / "sample_model.zip", entries)
