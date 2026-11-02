"""Quiets one known-harmless MCP SDK log record (mcp 1.30).

When a client closes its session (the connector does after every forwarded shop call, the agent at
the end of a run), the server's standalone SSE writer finds its stream already closed and logs
`ClosedResourceError` with a full traceback. The request itself succeeded. Only that exact record
is dropped; every other MCP error, including other errors from the same writer, is still logged.
"""

import logging

import anyio

LOGGER = "mcp.server.streamable_http"
MESSAGE = "Error in standalone SSE writer"


class ClosedSseWriterFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (record.getMessage() == MESSAGE and isinstance(exc, anyio.ClosedResourceError))


def quiet_closed_sse_writer() -> None:
    log = logging.getLogger(LOGGER)
    if not any(isinstance(f, ClosedSseWriterFilter) for f in log.filters):
        log.addFilter(ClosedSseWriterFilter())
