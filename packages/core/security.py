"""ApexGraph Security Validations and Guardrails

Implements strict path traversal prevention, SSRF IP blocking,
and secret redaction in logs.
"""

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

FORBIDDEN_WINDOWS_PATHS = [
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\system volume information",
]

FORBIDDEN_UNIX_PATHS = [
    "/etc",
    "/root",
    "/var/run",
    "/sys",
    "/proc",
    "/dev",
]

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)(ghp_[A-Za-z0-9_]{36}|github_pat_[A-Za-z0-9_]{82})"),
    re.compile(r"(?i)(sk-[A-Za-z0-9]{32,})"),
    re.compile(r"(?i)(AKIA[0-9A-Z]{16})"),
]


class SecurityValidationError(ValueError):
    """Raised when security boundary validation fails."""
    pass


def validate_local_path(path_str: str) -> str:
    """Validates local filesystem path, preventing directory traversal and sensitive folder access."""
    if not path_str or "\x00" in path_str:
        raise SecurityValidationError("Path contains invalid or null byte characters.")

    canonical = os.path.realpath(path_str).replace("\\", "/")
    lower = canonical.lower()

    for forbidden in FORBIDDEN_WINDOWS_PATHS:
        if lower.startswith(forbidden.replace("\\", "/")):
            raise SecurityValidationError(f"Access to system path '{canonical}' is strictly prohibited.")

    for forbidden in FORBIDDEN_UNIX_PATHS:
        if lower.startswith(forbidden):
            raise SecurityValidationError(f"Access to system path '{canonical}' is strictly prohibited.")

    if not os.path.exists(canonical):
        raise SecurityValidationError(f"Target path '{canonical}' does not exist.")

    if not os.path.isdir(canonical):
        raise SecurityValidationError(f"Target path '{canonical}' is not a directory.")

    return canonical


def validate_git_url(url_str: str) -> str:
    """Validates remote Git repository URL with SSRF protection against internal/cloud metadata networks."""
    if not url_str or "\x00" in url_str:
        raise SecurityValidationError("Invalid URL characters.")

    parsed = urlparse(url_str)
    if parsed.scheme.lower() != "https":
        raise SecurityValidationError(
            f"Unsupported protocol scheme '{parsed.scheme}'. Only 'https://' is permitted."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SecurityValidationError("Invalid URL: missing hostname.")

    # Block direct IP literals in URL if private
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SecurityValidationError(f"SSRF violation: Access to private IP '{hostname}' blocked.")
    except ValueError:
        pass  # Hostname is a domain name, proceed to DNS resolution

    # Resolve hostname to IPv4/IPv6
    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in resolved_ips:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)

            # Block loopback, RFC 1918 private, link-local (AWS metadata 169.254.169.254)
            if (
                ip_obj.is_loopback
                or ip_obj.is_private
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
            ):
                raise SecurityValidationError(
                    f"SSRF violation: Hostname '{hostname}' resolves to restricted IP '{ip_str}'."
                )
    except socket.gaierror as e:
        raise SecurityValidationError(f"DNS resolution failure for host '{hostname}': {e}")

    return url_str


def redact_secrets(text: str) -> str:
    """Redacts potential secrets, keys, and tokens from text or log messages."""
    redacted = text
    for pat in SECRET_PATTERNS:
        redacted = pat.sub(r"\1: [REDACTED]", redacted)
    return redacted
