"""SSRF-safe fetching for ``import_file_from_url``.

WHY this is not a one-line ``httpx.get``: the URL is chosen by a model, and the
request originates from inside the deployment's network. Without validation that
is a server-side request forgery primitive with access to everything the API can
reach — most importantly the cloud metadata service and private services on the
same network.

The rules, applied to the URL and to **every redirect hop**:

* ``https`` only, no userinfo, no non-default port games, bounded length;
* resolve DNS ourselves and refuse the whole host when ANY answer is private,
  loopback, link-local, multicast, reserved, unspecified, shared (CGNAT),
  documentation or a known cloud-metadata address — IPv4 and IPv6, including
  IPv4-mapped IPv6;
* connect to the **validated IP literal**, not the hostname, so a second DNS
  answer cannot rebind the connection. TLS still validates the certificate
  against the hostname (SNI + ``server_hostname``), so pinning does not weaken
  transport security;
* at most three redirects, each re-resolved and re-validated;
* a 15-second timeout and a streamed byte cap;
* send no cookies, no ``Authorization`` and no other inbound credentials.

The transport is injectable so tests can prove the pinning and redirect rules
without touching the network.
"""

import asyncio
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from typing import Protocol
from urllib.parse import SplitResult, unquote, urljoin, urlsplit

MAX_REDIRECTS = 3
CONNECT_TIMEOUT_SECONDS = 15
MAX_URL_LENGTH = 2048
#: Addresses that are never acceptable, spelled out even where a range check
#: would already catch them, because these are the names that matter in an
#: incident review.
_ALWAYS_BLOCKED = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS / Azure / GCP metadata
        ipaddress.ip_address("169.254.170.2"),  # ECS task metadata
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
        ipaddress.ip_address("192.0.0.192"),  # Oracle Cloud metadata
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IPv6 metadata
    }
)


class UrlImportRejected(Exception):
    """The URL cannot be fetched safely. The message is user-facing."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class FetchedFile:
    data: bytes
    file_name: str | None
    content_type: str | None


class _Transport(Protocol):
    async def get(
        self,
        *,
        host: str,
        ip: str,
        port: int,
        path: str,
        timeout: float,
        max_bytes: int,
    ) -> HttpResult: ...


Resolver = Callable[[str, int], Awaitable[list[str]]]


def _blocked(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        # An unparseable answer is not an address we can trust.
        return True
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if parsed in _ALWAYS_BLOCKED:
        return True
    return (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        # ``is_global`` also excludes shared address space (100.64/10) and the
        # documentation ranges, which ``is_private`` alone does not.
        or not parsed.is_global
    )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection that dials ``ip`` but validates ``host``'s certificate.

    This is the pin: the socket goes to the address that was just validated, so
    a second DNS lookup inside ``create_connection`` cannot redirect it. TLS is
    unchanged — ``server_hostname`` keeps SNI and certificate verification bound
    to the hostname the user's URL named.
    """

    def __init__(self, host: str, ip: str, port: int, *, timeout: float) -> None:
        context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip
        # Kept here rather than reached through the base class: ``_context`` is
        # private to ``http.client`` and not part of its typed surface.
        self._ssl_context = context

    def connect(self) -> None:  # pragma: no cover - exercised against real TLS
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)


def _get_sync(
    *, host: str, ip: str, port: int, path: str, timeout: float, max_bytes: int
) -> HttpResult:
    connection = _PinnedHTTPSConnection(host, ip, port, timeout=timeout)
    try:
        connection.request(
            "GET",
            path,
            headers={
                # Only a product identifier and an accept header: no cookies, no
                # Authorization, nothing from the inbound request.
                "User-Agent": "Transform-URL-Import/1.0",
                "Accept": "*/*",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise UrlImportRejected(
                "That file is larger than this server accepts for a URL import."
            )
        return HttpResult(status=response.status, headers=headers, body=body)
    finally:
        connection.close()


class PinnedHTTPSFetcher:
    """Fetch an https URL with DNS pinning and SSRF refusal."""

    def __init__(
        self,
        *,
        max_bytes: int,
        resolver: Resolver | None = None,
        transport: _Transport | None = None,
        timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._max_bytes = max_bytes
        self._resolver = resolver or self._system_resolver
        self._transport: _Transport = transport or _DefaultTransport()
        self._timeout = timeout

    async def fetch(self, url: str) -> FetchedFile:
        current = self._validate(url)
        for _ in range(MAX_REDIRECTS + 1):
            host = current.hostname
            assert host is not None  # guaranteed by _validate
            port = current.port or 443
            addresses = await self._resolver(host, port)
            if not addresses:
                raise UrlImportRejected("That URL's host could not be resolved.")
            blocked = [address for address in addresses if _blocked(address)]
            if blocked:
                # Refuse the whole host rather than "pick a good address": a
                # hostname that answers with an internal address is either
                # misconfigured or deliberately rebinding.
                raise UrlImportRejected("That URL is not allowed.")
            result = await self._transport.get(
                host=host,
                ip=addresses[0],
                port=port,
                path=self._request_target(current),
                timeout=self._timeout,
                max_bytes=self._max_bytes,
            )
            if result.status in (301, 302, 303, 307, 308):
                location = result.headers.get("location")
                if not location:
                    raise UrlImportRejected("That URL redirected without a destination.")
                current = self._validate(urljoin(current.geturl(), location))
                continue
            if result.status != 200:
                raise UrlImportRejected(
                    "That URL could not be downloaded (the server returned an error)."
                )
            if len(result.body) > self._max_bytes:
                # The default transport already stops reading at the cap; this
                # keeps the bound true for any transport (and for a response
                # that ignores it).
                raise UrlImportRejected(
                    "That file is larger than this server accepts for a URL import."
                )
            return FetchedFile(
                data=result.body,
                file_name=self._file_name(result.headers, current.geturl()),
                content_type=result.headers.get("content-type"),
            )
        raise UrlImportRejected("That URL redirected too many times.")

    @staticmethod
    def _validate(url: str) -> SplitResult:
        if len(url) > MAX_URL_LENGTH:
            raise UrlImportRejected("That URL is too long.")
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise UrlImportRejected("Only https:// URLs can be imported.")
        if parts.username or parts.password:
            raise UrlImportRejected("That URL is not allowed.")
        if not parts.hostname:
            raise UrlImportRejected("That URL has no host.")
        if parts.port is not None and not (1 <= parts.port <= 65535):
            raise UrlImportRejected("That URL has an invalid port.")
        return parts

    @staticmethod
    def _request_target(parts: SplitResult) -> str:
        path = parts.path or "/"
        return f"{path}?{parts.query}" if parts.query else path

    @staticmethod
    def _file_name(headers: Mapping[str, str], url: str) -> str | None:
        disposition = headers.get("content-disposition")
        if disposition:
            message = Message()
            message["content-disposition"] = disposition
            name = message.get_filename()
            if name:
                return name
        leaf = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        return leaf or None

    @staticmethod
    async def _system_resolver(host: str, port: int) -> list[str]:
        def resolve() -> list[str]:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            return list(dict.fromkeys(str(info[4][0]) for info in infos))

        try:
            return await asyncio.to_thread(resolve)
        except OSError as exc:
            raise UrlImportRejected("That URL's host could not be resolved.") from exc


class _DefaultTransport:
    """The real network transport: one pinned HTTPS GET per hop."""

    async def get(
        self,
        *,
        host: str,
        ip: str,
        port: int,
        path: str,
        timeout: float,
        max_bytes: int,
    ) -> HttpResult:
        return await asyncio.to_thread(
            _get_sync,
            host=host,
            ip=ip,
            port=port,
            path=path,
            timeout=timeout,
            max_bytes=max_bytes,
        )
