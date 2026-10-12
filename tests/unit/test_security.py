"""Unit Tests for Security Validations and SSRF Defense
"""

import pytest
from packages.core.security import (
    SecurityValidationError,
    redact_secrets,
    validate_git_url,
    validate_local_path,
)


def test_validate_local_path_safe(tmp_path):
    safe_dir = tmp_path / "valid_repo"
    safe_dir.mkdir()
    validated = validate_local_path(str(safe_dir))
    assert str(safe_dir).replace("\\", "/").lower() in validated.lower()


def test_validate_local_path_traversal_and_forbidden():
    with pytest.raises(SecurityValidationError, match="does not exist"):
        validate_local_path("/non/existent/path/here")

    # Forbidden Windows paths
    with pytest.raises(SecurityValidationError, match="prohibited"):
        validate_local_path("C:\\Windows")


def test_validate_git_url_ssrf():
    # Dangerous schemes
    with pytest.raises(SecurityValidationError, match="Only 'https://' is permitted"):
        validate_git_url("file:///etc/passwd")

    with pytest.raises(SecurityValidationError, match="Only 'https://' is permitted"):
        validate_git_url("http://github.com/repo")

    # Private loopback IP
    with pytest.raises(SecurityValidationError, match="SSRF violation"):
        validate_git_url("https://127.0.0.1/malicious.git")

    # Private RFC 1918 IP
    with pytest.raises(SecurityValidationError, match="SSRF violation"):
        validate_git_url("https://192.168.1.1/malicious.git")

    # AWS metadata endpoint
    with pytest.raises(SecurityValidationError, match="SSRF violation"):
        validate_git_url("https://169.254.169.254/latest/meta-data")


def test_redact_secrets():
    raw_log = "Error in auth: api_key='sk-abcdef1234567890abcdef1234567890' and token=ghp_123456789012345678901234567890123456"
    sanitized = redact_secrets(raw_log)
    assert "sk-abcdef" not in sanitized
    assert "ghp_12345" not in sanitized
    assert "[REDACTED]" in sanitized
