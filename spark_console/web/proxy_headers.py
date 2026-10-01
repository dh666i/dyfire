"""Apply forwarded request metadata only from explicitly trusted proxies.

The application is normally behind a reverse proxy, but it must also remain
safe when a client can connect to the ASGI server directly.  In particular,
``X-Forwarded-*`` headers from an untrusted peer must never change the request
scheme or the address used by rate limiting and audit code.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable

from starlette.types import Receive, Scope, Send


class TrustedProxyMiddleware:
    """Resolve forwarded scheme/client IP from configured proxy networks.

    The proxy list is intentionally explicit.  The closest peer must belong
    to one of these networks before either forwarded value is considered.
    Proxies should strip client-supplied forwarding headers and append their
    own values.  For an X-Forwarded-For chain we walk from the closest proxy
    backwards and select the first address outside the trusted networks.
    """

    def __init__(
        self,
        app: Callable,
        trusted_proxy_cidrs: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.networks = tuple(
            self._parse_network(value) for value in trusted_proxy_cidrs
        )

    @staticmethod
    def _parse_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("trusted proxy CIDR must be a non-empty string")
        try:
            return ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as error:
            raise ValueError(f"invalid trusted proxy CIDR: {value!r}") from error

    @staticmethod
    def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            address = ipaddress.ip_address(value.strip())
        except (AttributeError, ValueError):
            return None
        # Treat IPv4-mapped IPv6 addresses consistently with IPv4 networks.
        if isinstance(address, ipaddress.IPv6Address):
            return address.ipv4_mapped or address
        return address

    def _trusted(
        self,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
    ) -> bool:
        if address is None:
            return False
        return any(
            address.version == network.version and address in network
            for network in self.networks
        )

    @staticmethod
    def _header(scope: Scope, name: bytes) -> str | None:
        values: list[str] = []
        for key, value in scope.get("headers", ()):
            if key.lower() == name:
                try:
                    values.append(value.decode("latin-1"))
                except UnicodeDecodeError:
                    continue
        return ",".join(values) if values else None

    def _forwarded_client(self, value: str) -> str | None:
        # Values are ordered from the original client toward this server.
        # Walking backwards skips known proxies and returns the nearest
        # untrusted hop, which is the address a rate limiter should use.
        tokens = [item.strip() for item in value.split(",")]
        if not tokens or any(not item for item in tokens):
            return None
        addresses = [self._address(item) for item in tokens]
        # A malformed hop is ambiguous.  Ignore the entire chain instead of
        # selecting a value around it that a caller could use to spoof limits.
        if any(address is None for address in addresses):
            return None
        for address in reversed(addresses):
            assert address is not None
            if not self._trusted(address):
                return str(address)
        return None

    @staticmethod
    def _forwarded_scheme(value: str) -> str | None:
        # The closest proxy's value is the final token in an appended chain.
        # Only the two schemes understood by the application are accepted.
        scheme = value.rsplit(",", 1)[-1].strip().lower()
        return scheme if scheme in {"http", "https"} else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and self.networks:
            peer = scope.get("client")
            peer_address = self._address(peer[0]) if peer else None
            if self._trusted(peer_address):
                forwarded_scheme = self._header(scope, b"x-forwarded-proto")
                if forwarded_scheme:
                    scheme = self._forwarded_scheme(forwarded_scheme)
                    if scheme:
                        scope["scheme"] = scheme

                forwarded_for = self._header(scope, b"x-forwarded-for")
                if forwarded_for:
                    client_address = self._forwarded_client(forwarded_for)
                    if client_address:
                        scope["client"] = (client_address, peer[1] if peer else 0)

        await self.app(scope, receive, send)
