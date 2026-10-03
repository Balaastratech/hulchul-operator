"""Exact-host navigation and explicit fixture-origin submission authority."""

from dataclasses import dataclass
from urllib.parse import urlsplit


def origin(url: str) -> tuple[str, str, int]:
    """Parse a network URL; reject credentials, ambiguous hosts and unsafe schemes."""
    if any(char.isspace() or char in "\\\x00" for char in url):
        raise ValueError("invalid URL")
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("network URL without credentials required")
    host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    return parts.scheme, host, parts.port or (443 if parts.scheme == "https" else 80)


@dataclass(frozen=True)
class DomainAllowlist:
    """Trusted configuration; model/page content cannot add entries."""

    hosts: frozenset[str]
    fixture_origins: frozenset[tuple[str, str, int]] = frozenset()

    @classmethod
    def from_urls(
        cls,
        urls: list[str],
        *,
        known_ats_urls: list[str] | None = None,
        control_plane_url: str | None = None,
        fixture_urls: list[str] | None = None,
    ) -> "DomainAllowlist":
        """Build from shortlisted apply URLs and explicit operator configuration."""
        navigation = (
            urls
            + (known_ats_urls or [])
            + ([control_plane_url] if control_plane_url else [])
        )
        fixtures = frozenset(origin(url) for url in (fixture_urls or []))
        return cls(frozenset(origin(url)[1] for url in navigation), fixtures)

    def permits(self, url: str) -> bool:
        """Check exact host; subdomains and deceptive suffixes grant no authority."""
        try:
            return origin(url)[1] in self.hosts
        except (ValueError, UnicodeError):
            return False

    def check_redirects(self, urls: list[str]) -> None:
        """Adapters must check each hop before following it, not after navigation."""
        if not urls or any(not self.permits(url) for url in urls):
            raise PermissionError("off-allowlist redirect blocked")

    def permits_submission(self, url: str) -> bool:
        """Submit authority exists only for explicitly configured fixture origins."""
        try:
            return self.permits(url) and origin(url) in self.fixture_origins
        except (ValueError, UnicodeError):
            return False
