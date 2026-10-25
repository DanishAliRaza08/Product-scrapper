"""URL validation that mitigates SSRF.

User-submitted URLs are only ever *parsed* to extract identifiers for a connector whose
host allow-list matches. When the platform must fetch a URL (e.g. copying a permitted
image), :func:`assert_public_http_url` resolves the host and rejects private, loopback,
link-local, multicast and reserved addresses.
"""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.errors import AppError

MAX_URL_LENGTH = 2048


class UnsafeUrl(AppError):
    code = "unsafe_url"


def normalise_url(raw: str) -> str:
    url = raw.strip()
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrl("URL is too long")
    if "://" not in url:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)", url):
            raise UnsafeUrl("Only http and https URLs are supported")
        url = "https://" + url
    parts = urlsplit(url)
    try:
        parts.port  # noqa: B018 - validates the port component
    except ValueError as exc:
        raise UnsafeUrl("URL has an invalid port") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrl("Only http and https URLs are supported")
    if not parts.hostname:
        raise UnsafeUrl("URL has no host")
    if parts.username or parts.password:
        raise UnsafeUrl("URLs containing credentials are not accepted")
    return url


def host_matches(hostname: str, allowed_suffixes: tuple[str, ...]) -> bool:
    host = hostname.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in allowed_suffixes)


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def assert_public_http_url(raw: str, *, resolver=socket.getaddrinfo) -> str:
    url = normalise_url(raw)
    if get_settings().allow_private_network_urls:
        return url
    host = urlsplit(url).hostname or ""
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = resolver(host, None)
        except socket.gaierror as exc:
            raise UnsafeUrl("Host could not be resolved") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses or not all(_is_public_ip(a) for a in addresses):
        raise UnsafeUrl("URL resolves to a private or reserved network address")
    return url
