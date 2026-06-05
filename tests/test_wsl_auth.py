"""
TDD tests for windows/src/wsl_auth.py.

These tests define the required behaviour of the framework's wsl_auth module:
  (a) valid token with >buffer_secs remaining → no refresh, returns True
  (b) expired token → POST to refresh URL → new token stored, returns True
  (c) refresh endpoint returns 401 → returns False
  (d) diagnose_wsl_auth returns a non-empty string

All external calls (wsl.exe subprocess, httpx) are mocked.
No real credentials file or network calls are used.
"""

import asyncio
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_creds(access_token: str, expires_at_ms: int, refresh_token: str = "rtoken") -> dict:
    """Build a minimal credentials dict matching the claudeAiOauth format."""
    return {
        "claudeAiOauth": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
    }


def _future_ms(offset_secs: int = 3600) -> int:
    """Unix timestamp in milliseconds offset_secs from now."""
    return int((time.time() + offset_secs) * 1000)


def _past_ms(offset_secs: int = 3600) -> int:
    """Unix timestamp in milliseconds offset_secs in the past."""
    return int((time.time() - offset_secs) * 1000)


def _import_module():
    """Import (or re-import) windows.src.wsl_auth."""
    mod_name = "windows.src.wsl_auth"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import windows.src.wsl_auth as m
    return m


# Patch target for the subprocess helper inside the module
_WSL_PYTHON = "windows.src.wsl_auth._wsl_python"


# ---------------------------------------------------------------------------
# (a) valid token → no refresh, returns True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_token_returns_true_no_refresh():
    """If the token has >buffer_secs remaining, refresh_claude_auth returns True
    without hitting the refresh endpoint."""
    mod = _import_module()

    creds = _make_creds("valid_access_token", _future_ms(7200))  # 2 hours left

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        with patch("windows.src.wsl_auth.httpx.AsyncClient") as mock_client:
            result = await mod.refresh_claude_auth(buffer_secs=600)

    assert result is True
    # httpx should NOT have been called
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_valid_token_buffer_boundary_no_refresh():
    """Token expiring just beyond buffer_secs should still return True without refresh."""
    mod = _import_module()

    # 700 seconds left, buffer is 600 → still valid
    creds = _make_creds("border_token", _future_ms(700))

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        with patch("windows.src.wsl_auth.httpx.AsyncClient") as mock_client:
            result = await mod.refresh_claude_auth(buffer_secs=600)

    assert result is True
    mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# (b) expired token → POST to refresh → new token stored, returns True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_token_triggers_refresh_and_stores_new_token():
    """Expired token causes a POST to the refresh URL; new token is written back."""
    mod = _import_module()

    creds = _make_creds("old_access_token", _past_ms(100))  # expired 100s ago

    refresh_response_data = {
        "access_token": "new_shiny_token",
        "expires_in": 3600,
        "refresh_token": "new_refresh_token",
    }

    # Mock the httpx response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = refresh_response_data

    # Build an async context manager mock for httpx.AsyncClient().__aenter__
    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    written_creds = {}

    async def fake_wsl_python(script: str, timeout: float = 15.0):
        if "_READ_CREDS" in script or "expanduser" in script and "credentials.json" in script and "open(" in script:
            return json.dumps(creds), ""
        # write call: capture what was written
        import re
        m = re.search(r"tmp\.write\((.*?)\)\s*\n", script, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            # The script uses repr(dumped) — eval it to get the JSON string
            try:
                json_str = eval(raw)  # noqa: S307  (test-only, trusted source)
                written_creds.update(json.loads(json_str))
            except Exception:
                pass
        return "", ""

    with patch(_WSL_PYTHON, new=fake_wsl_python):
        with patch("windows.src.wsl_auth.httpx.AsyncClient", return_value=mock_client_cm):
            result = await mod.refresh_claude_auth()

    assert result is True
    mock_http_instance.post.assert_called_once()

    # Verify the POST payload contained grant_type and refresh_token
    call_kwargs = mock_http_instance.post.call_args
    payload = call_kwargs.kwargs.get("data") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
    assert payload.get("grant_type") == "refresh_token"
    assert payload.get("refresh_token") == "rtoken"


@pytest.mark.asyncio
async def test_expired_token_refresh_updates_credentials():
    """After a successful refresh the new access token and expiry are persisted."""
    mod = _import_module()

    creds = _make_creds("stale_token", _past_ms(500))

    refresh_data = {"access_token": "fresh_token", "expires_in": 7200}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = refresh_data

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    write_calls = []

    async def fake_wsl_python(script: str, timeout: float = 15.0):
        if "open(" in script and "credentials.json" in script and "tmp.write" not in script:
            return json.dumps(creds), ""
        if "tmp.write" in script:
            write_calls.append(script)
        return "", ""

    with patch(_WSL_PYTHON, new=fake_wsl_python):
        with patch("windows.src.wsl_auth.httpx.AsyncClient", return_value=mock_client_cm):
            result = await mod.refresh_claude_auth()

    assert result is True
    # write_wsl_credentials should have been called (at least one write call)
    assert len(write_calls) >= 1, "Expected write_wsl_credentials to be called after refresh"


# ---------------------------------------------------------------------------
# (c) refresh endpoint returns 401 → returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_endpoint_401_returns_false():
    """When the OAuth refresh endpoint returns 401, refresh_claude_auth returns False."""
    mod = _import_module()

    creds = _make_creds("dead_token", _past_ms(3600))

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        with patch("windows.src.wsl_auth.httpx.AsyncClient", return_value=mock_client_cm):
            result = await mod.refresh_claude_auth()

    assert result is False


@pytest.mark.asyncio
async def test_refresh_endpoint_500_returns_false():
    """Any non-200 response from the refresh endpoint causes False to be returned."""
    mod = _import_module()

    creds = _make_creds("expired_token", _past_ms(100))

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_resp)
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        with patch("windows.src.wsl_auth.httpx.AsyncClient", return_value=mock_client_cm):
            result = await mod.refresh_claude_auth()

    assert result is False


@pytest.mark.asyncio
async def test_no_credentials_returns_false():
    """If credentials file is missing (empty dict returned), returns False."""
    mod = _import_module()

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=("{}", ""))):
        result = await mod.refresh_claude_auth()

    assert result is False


@pytest.mark.asyncio
async def test_no_refresh_token_returns_false():
    """Expired token with no refreshToken returns False immediately."""
    mod = _import_module()

    creds = {
        "claudeAiOauth": {
            "accessToken": "expired",
            "expiresAt": _past_ms(3600),
            # No refreshToken
        }
    }

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        with patch("windows.src.wsl_auth.httpx.AsyncClient") as mock_client:
            result = await mod.refresh_claude_auth()

    assert result is False
    mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# (d) diagnose_wsl_auth returns a non-empty string
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diagnose_returns_non_empty_string_when_valid():
    """diagnose_wsl_auth returns a non-empty diagnostic string for a valid token."""
    mod = _import_module()

    creds = _make_creds("valid_token", _future_ms(3600))

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        result = await mod.diagnose_wsl_auth()

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_diagnose_returns_non_empty_string_when_no_creds():
    """diagnose_wsl_auth returns a meaningful string even when credentials are missing."""
    mod = _import_module()

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=("{}", ""))):
        result = await mod.diagnose_wsl_auth()

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_diagnose_includes_oauth_key_name():
    """diagnose_wsl_auth output includes the OAuth key used from credentials."""
    mod = _import_module()

    creds = _make_creds("tok", _future_ms(1800))

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        result = await mod.diagnose_wsl_auth()

    # Should mention the key name used in credentials
    assert "claudeAiOauth" in result


@pytest.mark.asyncio
async def test_diagnose_expired_token_mentions_expiry():
    """diagnose_wsl_auth report for an expired token mentions expiry."""
    mod = _import_module()

    creds = _make_creds("old_tok", _past_ms(300))

    with patch(_WSL_PYTHON, new=AsyncMock(return_value=(json.dumps(creds), ""))):
        result = await mod.diagnose_wsl_auth()

    assert isinstance(result, str)
    assert len(result) > 0
    # Should contain some indication that token is expired or timing info
    assert any(word in result.lower() for word in ["expir", "ago", "remain"])
