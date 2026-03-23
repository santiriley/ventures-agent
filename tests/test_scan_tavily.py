"""
tests/test_scan_tavily.py — Tests for scan_tavily_queries() extra_queries param.
Mocks requests.post to avoid real Tavily calls.
"""

from unittest.mock import patch, MagicMock
import config
from monitor.batches import scan_tavily_queries


def _mock_tavily_response(results=None):
    """Return a mock requests.Response with Tavily-shaped JSON."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": results or [
            {"title": "Test Result", "content": "A startup in Costa Rica."}
        ]
    }
    mock_resp.raise_for_status.return_value = None
    return mock_resp


@patch("time.sleep")  # skip inter-query delays in all tests
@patch("requests.post")
@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
def test_extra_queries_appended(mock_post, mock_sleep):
    """extra_queries should be run after the configured queries."""
    mock_post.return_value = _mock_tavily_response()

    base_count = len(config.TAVILY_MONITOR_QUERIES)
    extra = ["embedded finance Central America 2025", "BaaS startup Dominican Republic"]

    results = scan_tavily_queries(extra_queries=extra)

    # Should have called Tavily for base queries + 2 extra
    assert mock_post.call_count == base_count + len(extra)


@patch("time.sleep")
@patch("requests.post")
@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
def test_extra_queries_tagged_correctly(mock_post, mock_sleep):
    """Dynamic queries should get 'tavily:extra:{i}' tags."""
    mock_post.return_value = _mock_tavily_response()

    results = scan_tavily_queries(extra_queries=["my dynamic query"])

    source_tags = [tag for _, tag in results]
    assert "tavily:extra:0" in source_tags


@patch("time.sleep")
@patch("requests.post")
@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
def test_extra_queries_capped_at_8(mock_post, mock_sleep):
    """extra_queries must be capped at 8 to protect Tavily quota."""
    mock_post.return_value = _mock_tavily_response()

    extra = [f"query {i}" for i in range(15)]  # 15 queries, should be capped at 8
    scan_tavily_queries(extra_queries=extra)

    base_count = len(config.TAVILY_MONITOR_QUERIES)
    assert mock_post.call_count == base_count + 8


@patch("time.sleep")
@patch("requests.post")
@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
def test_no_extra_queries_runs_base_only(mock_post, mock_sleep):
    """Without extra_queries, only the configured TAVILY_MONITOR_QUERIES run."""
    mock_post.return_value = _mock_tavily_response()

    scan_tavily_queries()

    assert mock_post.call_count == len(config.TAVILY_MONITOR_QUERIES)


@patch("time.sleep")
@patch("requests.post")
@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
def test_query_refinements_still_apply_with_extra(mock_post, mock_sleep):
    """query_refinements should still be applied when extra_queries is also passed."""
    mock_post.return_value = _mock_tavily_response()

    # Just check it doesn't crash and runs the expected number of queries
    base_count = len(config.TAVILY_MONITOR_QUERIES)
    extra = ["extra query"]
    results = scan_tavily_queries(
        query_refinements={"tavily:f6s": "costa rica"},
        extra_queries=extra,
    )
    assert mock_post.call_count == base_count + 1
