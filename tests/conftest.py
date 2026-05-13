"""Shared pytest setup.

Tests should not write to ~/.shelby/* on a developer machine. Each test
that touches memory or skills gets a tmp_path-rooted override via
fixtures here.
"""
from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture
def tmp_memory(tmp_path, monkeypatch):
    """Point memory.py at an isolated jsonl path for the test."""
    path = tmp_path / "memory.jsonl"
    monkeypatch.setenv("SHELBY_MEMORY_PATH", str(path))
    # memory module reads the env var lazily on each call, so no reimport
    # needed.
    yield path


@pytest.fixture
def tmp_skills_dir(tmp_path, monkeypatch):
    """Point skills.py at an isolated directory for the test."""
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setenv("SHELBY_SKILLS_DIR", str(d))
    yield d
