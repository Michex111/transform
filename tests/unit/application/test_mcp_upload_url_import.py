"""``import_file_from_url``'s fetcher: SSRF refusal, pinning, redirects, caps.

The URL is model-chosen and the request comes from inside the deployment, so
these tests are the security boundary: private/metadata answers are refused
(including one bad answer among good ones), the validated IP is the one dialled,
every redirect is re-validated, and no inbound credential is forwarded.

The resolver and transport are injected; nothing here touches the network.
"""

import asyncio

import pytest

from src.application.services.mcp.url_import import (
    HttpResult,
    PinnedHTTPSFetcher,
    UrlImportRejected,
)

PUBLIC = "93.184.216.34"


class Dns:
    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.mapping.get(host, [])


class Transport:
    def __init__(self, responses: list[HttpResult]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def get(self, **kwargs) -> HttpResult:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def ok(body: bytes = b"data", headers: dict[str, str] | None = None) -> HttpResult:
    return HttpResult(status=200, headers=headers or {}, body=body)


def redirect(location: str) -> HttpResult:
    return HttpResult(status=302, headers={"location": location}, body=b"")


def build(
    *,
    mapping: dict[str, list[str]] | None = None,
    responses: list[HttpResult] | None = None,
    max_bytes: int = 1024,
) -> tuple[PinnedHTTPSFetcher, Dns, Transport]:
    dns = Dns(mapping if mapping is not None else {"files.example": [PUBLIC]})
    transport = Transport(responses or [ok()])
    fetcher = PinnedHTTPSFetcher(max_bytes=max_bytes, resolver=dns, transport=transport)
    return fetcher, dns, transport


def test_only_https_is_fetched() -> None:
    async def run() -> None:
        fetcher, _dns, transport = build()
        for url in (
            "http://files.example/a.pdf",
            "ftp://files.example/a.pdf",
            "file:///etc/passwd",
            "https://user:pass@files.example/a.pdf",
        ):
            with pytest.raises(UrlImportRejected):
                await fetcher.fetch(url)
        assert transport.calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
    ],
)
def test_private_and_metadata_addresses_are_refused(address: str) -> None:
    async def run() -> None:
        fetcher, _dns, transport = build(mapping={"files.example": [address]})
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")
        assert transport.calls == []

    asyncio.run(run())


def test_one_bad_answer_refuses_the_whole_host() -> None:
    """A split answer is a rebinding attempt, not something to cherry-pick."""

    async def run() -> None:
        fetcher, _dns, transport = build(mapping={"files.example": [PUBLIC, "127.0.0.1"]})
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")
        assert transport.calls == []

    asyncio.run(run())


def test_the_validated_address_is_the_one_dialled() -> None:
    async def run() -> None:
        fetcher, _dns, transport = build(
            mapping={"files.example": ["93.184.216.35", "93.184.216.34"]},
        )
        fetched = await fetcher.fetch("https://files.example/dir/a.pdf?sig=1")
        assert fetched.data == b"data"
        call = transport.calls[0]
        assert call["host"] == "files.example"
        assert call["ip"] == "93.184.216.35"
        assert call["port"] == 443
        assert call["path"] == "/dir/a.pdf?sig=1"
        # No inbound credential can be forwarded: the transport is not given
        # headers, cookies or an authorization value at all.
        assert set(call) == {"host", "ip", "port", "path", "timeout", "max_bytes"}

    asyncio.run(run())


def test_a_redirect_to_a_private_host_is_refused() -> None:
    async def run() -> None:
        fetcher, dns, _transport = build(
            mapping={"files.example": [PUBLIC], "internal.example": ["10.0.0.5"]},
            responses=[redirect("https://internal.example/secret")],
        )
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")
        assert dns.calls == [("files.example", 443), ("internal.example", 443)]

    asyncio.run(run())


def test_a_redirect_to_plain_http_is_refused() -> None:
    async def run() -> None:
        fetcher, _dns, _transport = build(
            responses=[redirect("http://files.example/a.pdf")]
        )
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")

    asyncio.run(run())


def test_more_than_three_redirects_is_refused() -> None:
    async def run() -> None:
        fetcher, _dns, _transport = build(
            responses=[redirect("https://files.example/next") for _ in range(4)]
        )
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")

    asyncio.run(run())


def test_a_body_over_the_cap_is_refused() -> None:
    async def run() -> None:
        fetcher, _dns, _transport = build(
            max_bytes=4, responses=[ok(body=b"12345")]
        )
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")

    asyncio.run(run())


def test_an_error_status_is_not_returned_as_a_file() -> None:
    async def run() -> None:
        fetcher, _dns, _transport = build(
            responses=[HttpResult(status=404, headers={}, body=b"nope")]
        )
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")

    asyncio.run(run())


def test_the_name_prefers_content_disposition_then_the_url() -> None:
    async def run() -> None:
        fetcher, _dns, _transport = build(
            responses=[
                ok(headers={"content-disposition": 'attachment; filename="invoice.pdf"'}),
            ]
        )
        fetched = await fetcher.fetch("https://files.example/download")
        assert fetched.file_name == "invoice.pdf"

        fetcher, _dns, _transport = build(
            responses=[ok(headers={"content-disposition": "attachment"})]
        )
        fetched = await fetcher.fetch("https://files.example/dir/report%20final.pdf")
        assert fetched.file_name == "report final.pdf"

    asyncio.run(run())


def test_an_unresolvable_host_is_refused() -> None:
    async def run() -> None:
        fetcher, _dns, transport = build(mapping={})
        with pytest.raises(UrlImportRejected):
            await fetcher.fetch("https://files.example/a.pdf")
        assert transport.calls == []

    asyncio.run(run())
