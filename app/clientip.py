"""Client IP resolution for rate limiting, with an allow-listed proxy chain."""

from __future__ import annotations

import ipaddress
import logging
import os

from fastapi import Request

log = logging.getLogger("arena")


def _parse_trusted_proxies(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse a comma-separated list of IPs / CIDR blocks into networks.

    Bare IPs are treated as /32 (IPv4) or /128 (IPv6). Malformed entries are
    skipped with a warning so a fat-fingered value doesn't crash boot; the
    net effect of a skipped entry is that the peer won't be trusted and its
    X-Forwarded-For header will be ignored, which is the safe default.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as e:
            log.warning("ignoring malformed TRUSTED_PROXIES entry %r: %s", token, e)
    return networks


# Reverse proxies whose X-Forwarded-For headers we're willing to trust.
# Empty (default) means: NEVER honor XFF — every request keys off its
# socket peer, so an untrusted client can't spoof its way past the battle
# rate limit. Operators fronting the app with Caddy / nginx / Tailscale
# Funnel should list the proxy's egress IPs or CIDRs here.
TRUSTED_PROXIES = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))
if not TRUSTED_PROXIES:
    log.info("TRUSTED_PROXIES not set; X-Forwarded-For headers will be ignored")


def get_client_ip(request: Request) -> str:
    """Return the client IP to key rate-limits on.

    X-Forwarded-For is only honored when the direct peer is in TRUSTED_PROXIES.
    Otherwise a client on the open internet could rotate the header to bypass
    the per-IP battle rate limit — the direct-port deployment shipped with no
    reverse proxy at all, and the limiter was the only thing between an
    authenticated caller and unbounded provider spend.
    """
    peer_host = request.client.host if request.client else None

    if peer_host and TRUSTED_PROXIES:
        try:
            peer_ip = ipaddress.ip_address(peer_host)
            if any(peer_ip in net for net in TRUSTED_PROXIES):
                forwarded = request.headers.get("x-forwarded-for")
                if forwarded:
                    # The leftmost IP is the original client; everything after
                    # is the proxy chain we just verified.
                    return forwarded.split(",")[0].strip()
        except ValueError:
            # Peer host wasn't an IP (unix socket etc.); fall through and key
            # on the raw peer string below.
            pass

    return peer_host or "unknown"
