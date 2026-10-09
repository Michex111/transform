"""Developer observability domain: request events, buckets and MCP tool calls.

Pure rules only — no storage, no HTTP, no clock. Everything here is a value
object or a small computation, so the aggregation semantics (what counts as an
error, how a bucket is sized, how a percentile is interpolated) can be unit
tested without a database, a request, or a running server.
"""
