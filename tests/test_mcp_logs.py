"""mcp 1.30 logs a traceback when a client closes its session while the server's standalone SSE
writer is still open (every short connector call to the shop). Only that record is dropped."""

import logging

import anyio

from intentlock.mcp_logs import ClosedSseWriterFilter, quiet_closed_sse_writer


def record(msg, exc):
    try:
        raise exc
    except Exception as e:
        info = (type(e), e, e.__traceback__)
    return logging.LogRecord("mcp.server.streamable_http", logging.ERROR, "x", 1, msg, (), info)


def test_drops_only_the_closed_stream_error_of_the_sse_writer():
    f = ClosedSseWriterFilter()
    assert not f.filter(record("Error in standalone SSE writer", anyio.ClosedResourceError()))
    assert f.filter(record("Error in standalone SSE writer", RuntimeError("real")))
    assert f.filter(record("Error handling POST request", anyio.ClosedResourceError()))
    plain = logging.LogRecord(
        "mcp", logging.ERROR, "x", 1, "Error in standalone SSE writer", (), None
    )
    assert f.filter(plain)


def test_installing_twice_adds_one_filter():
    quiet_closed_sse_writer()
    quiet_closed_sse_writer()
    log = logging.getLogger("mcp.server.streamable_http")
    assert sum(isinstance(x, ClosedSseWriterFilter) for x in log.filters) == 1


def test_shop_and_gateway_install_it():
    from pathlib import Path

    from shop.app import create_app
    from shop.store import Store

    log = logging.getLogger("mcp.server.streamable_http")
    log.filters.clear()
    create_app(Store(), "k")
    assert any(isinstance(x, ClosedSseWriterFilter) for x in log.filters)
    api = (Path(__file__).resolve().parent.parent / "intentlock" / "api.py").read_text()
    assert "quiet_closed_sse_writer()" in api.split("def create_app(")[1].split("\ndef ")[0]
