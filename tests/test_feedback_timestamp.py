"""
tests/test_feedback_timestamp.py — Unit tests for Phase 2: Feedback timestamp gate.

Tests that last_feedback_run.json is created/read and that _fetch_notion_outcomes()
receives the correct `since` parameter.  All Notion API calls are mocked.
"""

from __future__ import annotations

import json
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import config
from feedback import (
    _load_last_run_timestamp,
    _save_last_run_timestamp,
    _LAST_RUN_FILE,
)


@pytest.fixture(autouse=True)
def clean_last_run_file(tmp_path, monkeypatch):
    """Each test gets a fresh _LAST_RUN_FILE in a temp directory."""
    import feedback as fb
    fake_path = tmp_path / "last_feedback_run.json"
    monkeypatch.setattr(fb, "_LAST_RUN_FILE", fake_path)
    yield fake_path


class TestTimestampHelpers:
    def test_load_returns_none_when_missing(self, clean_last_run_file):
        """No file → returns None (first run)."""
        assert _load_last_run_timestamp() is None

    def test_save_creates_file(self, clean_last_run_file):
        """_save_last_run_timestamp() creates the JSON file."""
        import feedback as fb
        # Patch at the module level so the monkeypatched path is used
        fb._save_last_run_timestamp()
        assert clean_last_run_file.exists()

    def test_save_and_load_roundtrip(self, clean_last_run_file):
        """Save then load returns the same timestamp (ISO format)."""
        import feedback as fb
        fb._save_last_run_timestamp()
        ts = fb._load_last_run_timestamp()
        assert ts is not None
        # Must be a valid ISO datetime string
        datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")

    def test_load_handles_corrupt_file(self, clean_last_run_file):
        """Corrupt JSON file → returns None (fail open)."""
        clean_last_run_file.write_text("not valid json", encoding="utf-8")
        import feedback as fb
        assert fb._load_last_run_timestamp() is None


class TestFetchNotionOutcomesSinceFilter:
    """Verify that _fetch_notion_outcomes passes `since` correctly to the Notion API."""

    def _make_mock_response(self, leads=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "results": leads or [],
            "has_more": False,
        }
        return resp

    def test_no_since_uses_or_filter(self):
        """Without `since`, filter is a plain OR on Status."""
        from feedback import _fetch_notion_outcomes
        with (
            patch("feedback.config.get_key", return_value="fake-db-id"),
            patch("feedback.requests.post") as mock_post,
        ):
            mock_post.return_value = self._make_mock_response()
            _fetch_notion_outcomes(since=None)

        body = mock_post.call_args[1]["json"]
        assert "or" in body["filter"]
        assert "and" not in body["filter"]

    def test_with_since_uses_and_filter(self):
        """With `since`, filter wraps OR in AND with last_edited_time."""
        from feedback import _fetch_notion_outcomes
        with (
            patch("feedback.config.get_key", return_value="fake-db-id"),
            patch("feedback.requests.post") as mock_post,
        ):
            mock_post.return_value = self._make_mock_response()
            _fetch_notion_outcomes(since="2026-03-01T00:00:00Z")

        body = mock_post.call_args[1]["json"]
        assert "and" in body["filter"]
        filters = body["filter"]["and"]
        ts_filter = next((f for f in filters if "timestamp" in f), None)
        assert ts_filter is not None
        assert ts_filter["last_edited_time"]["after"] == "2026-03-01T00:00:00Z"


class TestAutoApplyTimestampIntegration:
    """Test that run(auto_apply_only=True) reads/writes the timestamp file."""

    def _notion_empty_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [], "has_more": False}
        return resp

    def test_first_run_saves_timestamp(self, clean_last_run_file):
        """On first run (no existing file), timestamp file is created after completion."""
        import feedback as fb

        with (
            patch("feedback.config.get_key", return_value="fake"),
            patch("feedback.requests.post", return_value=self._notion_empty_response()),
        ):
            fb.run(auto_apply_only=True)

        assert clean_last_run_file.exists()
        data = json.loads(clean_last_run_file.read_text())
        assert "last_run" in data

    def test_second_run_passes_since_to_notion(self, clean_last_run_file):
        """On second run, the saved timestamp is passed to _fetch_notion_outcomes."""
        import feedback as fb

        # Pre-populate with a known timestamp
        clean_last_run_file.write_text(
            json.dumps({"last_run": "2026-03-01T12:00:00Z"}), encoding="utf-8"
        )

        with (
            patch("feedback.config.get_key", return_value="fake"),
            patch("feedback.requests.post") as mock_post,
        ):
            mock_post.return_value = self._notion_empty_response()
            fb.run(auto_apply_only=True)

        body = mock_post.call_args[1]["json"]
        # Should have AND filter with last_edited_time
        assert "and" in body["filter"]
        ts_filter = next(
            (f for f in body["filter"]["and"] if "timestamp" in f), None
        )
        assert ts_filter is not None
        assert ts_filter["last_edited_time"]["after"] == "2026-03-01T12:00:00Z"
