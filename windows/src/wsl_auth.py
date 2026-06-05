"""
wsl_auth.py — Proactive OAuth token refresh for the WSL claude CLI.

The claude CLI stores credentials at ~/.claude/.credentials.json.
Access tokens have a short TTL (typically a few hours); refresh tokens
are long-lived. This module refreshes the access token automatically
before it expires so the bot never hits a 401 mid-run.

If the refresh token itself is dead (rare — happens after extended disuse
or a remote logout), refresh_claude_auth() returns False and the caller
is expected to send a Telegram alert asking the user to re-authenticate.

Configuration (optional, in nanobot.config.json under "auth"):
    oauth_refresh_url   Override the default refresh endpoint
    oauth_client_id     Provide a client_id if the server requires it
    refresh_buffer_secs Refresh when token has fewer than N seconds left (default 600)
"""

import asyncio
import json
import logging
import subprocess
import time

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Claude Code CLI uses the claude.ai OAuth system.
# This endpoint accepts a standard OAuth 2.0 refresh_token grant.
DEFAULT_REFRESH_URL = "https://claude.ai/api/auth/oauth/token"

# Refresh when the token has fewer than this many seconds remaining.
DEFAULT_BUFFER_SECS = 600  # 10 minutes

# ---------------------------------------------------------------------------
# WSL helpers — run Python snippets inside WSL to read/write credentials
# ---------------------------------------------------------------------------

_READ_CREDS = r"""
import json, os, sys
p = os.path.expanduser("~/.claude/.credentials.json")
if os.path.exists(p):
    with open(p) as f:
        print(json.dumps(json.load(f)))
else:
    sys.stderr.write("credentials file not found\n")
    print("{}")
"""


async def _wsl_python(script: str, timeout: float = 15.0) -> tuple[str, str]:
    """Run a Python snippet inside WSL; return (stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "python3", "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode().strip(), stderr.decode().strip()


async def get_wsl_credentials() -> dict:
    """Read ~/.claude/.credentials.json from WSL. Returns {} on any error."""
    try:
        stdout, stderr = await _wsl_python(_READ_CREDS)
        if stderr:
            logger.debug("wsl_auth: get_wsl_credentials stderr: %s", stderr)
        return json.loads(stdout) if stdout else {}
    except Exception as exc:
        logger.warning("wsl_auth: could not read WSL credentials: %s", exc)
        return {}


async def write_wsl_credentials(creds: dict) -> None:
    """Atomically overwrite ~/.claude/.credentials.json in WSL."""
    dumped = json.dumps(creds, indent=2)
    # Use a temp file + rename for atomicity
    script = f"""
import json, os, tempfile, shutil
target = os.path.expanduser("~/.claude/.credentials.json")
os.makedirs(os.path.dirname(target), exist_ok=True)
with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(target),
                                  delete=False, suffix=".tmp") as tmp:
    tmp.write({repr(dumped)})
    tmp_path = tmp.name
shutil.move(tmp_path, target)
"""
    stdout, stderr = await _wsl_python(script)
    if stderr:
        logger.warning("wsl_auth: write_wsl_credentials stderr: %s", stderr)


# ---------------------------------------------------------------------------
# Token expiry helpers
# ---------------------------------------------------------------------------

def _find_oauth_block(creds: dict) -> tuple[dict, str]:
    """
    Return (oauth_dict, key_name).
    Claude Code has used different key names across versions; try all known ones.
    """
    for key in ("claudeAiOauth", "oauthToken", "oauth", "claudeAuth"):
        if isinstance(creds.get(key), dict):
            return creds[key], key
    return {}, ""


def _expires_at_seconds(oauth: dict) -> float:
    """Normalise expiresAt to Unix seconds (it may be stored as ms)."""
    raw = oauth.get("expiresAt", 0)
    if not raw:
        return 0.0
    # Values > 1e12 are almost certainly milliseconds (year ~2001+ in ms)
    return float(raw) / 1000 if raw > 1e11 else float(raw)


def _token_is_valid(oauth: dict, buffer_secs: int) -> bool:
    """True if the access token still has at least buffer_secs left."""
    exp = _expires_at_seconds(oauth)
    return exp > 0 and time.time() < exp - buffer_secs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def refresh_claude_auth(
    refresh_url: str = DEFAULT_REFRESH_URL,
    client_id: str | None = None,
    buffer_secs: int = DEFAULT_BUFFER_SECS,
) -> bool:
    """
    Ensure the WSL claude CLI has a valid access token.

    1. Read ~/.claude/.credentials.json from WSL.
    2. If the token is still valid (>= buffer_secs remaining), return True immediately.
    3. If expired, attempt refresh via the OAuth refresh_token grant.
    4. Write the new token back; return True.
    5. On any failure, log the reason and return False.

    Returns:
        True  — credentials are valid (either already were, or just refreshed)
        False — could not obtain a valid token; caller should alert the user
    """
    creds = await get_wsl_credentials()

    if not creds:
        logger.error("wsl_auth: no credentials file — user must run `claude auth login` in WSL")
        return False

    oauth, key = _find_oauth_block(creds)

    if not oauth:
        logger.error(
            "wsl_auth: credentials file has no recognised OAuth block. "
            "Top-level keys: %s", list(creds)
        )
        return False

    logger.debug(
        "wsl_auth: key=%s  expiresAt=%s  hasRefreshToken=%s",
        key, oauth.get("expiresAt"), bool(oauth.get("refreshToken")),
    )

    # Still valid — nothing to do
    if _token_is_valid(oauth, buffer_secs):
        logger.debug("wsl_auth: token valid; %.0fs remaining", _expires_at_seconds(oauth) - time.time())
        return True

    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        logger.error("wsl_auth: access token expired and no refresh_token present")
        return False

    logger.info("wsl_auth: access token expiring — refreshing via %s", refresh_url)

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_id:
        payload["client_id"] = client_id

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                refresh_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:
        logger.error("wsl_auth: HTTP error during refresh: %s", exc)
        return False

    if resp.status_code != 200:
        logger.error(
            "wsl_auth: refresh endpoint returned HTTP %d: %s",
            resp.status_code, resp.text[:300],
        )
        return False

    try:
        data = resp.json()
    except Exception:
        logger.error("wsl_auth: refresh response is not JSON: %s", resp.text[:200])
        return False

    # Update the in-memory oauth block
    oauth["accessToken"] = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    oauth["expiresAt"] = int((time.time() + expires_in) * 1000)  # store as ms
    if "refresh_token" in data:
        oauth["refreshToken"] = data["refresh_token"]

    creds[key] = oauth
    await write_wsl_credentials(creds)
    logger.info("wsl_auth: token refreshed — next expiry in %ds", expires_in)
    return True


async def diagnose_wsl_auth() -> str:
    """
    Return a human-readable diagnostic string (safe to send to Telegram).
    Does NOT include secret values.
    """
    creds = await get_wsl_credentials()
    if not creds:
        return "❌ No credentials file found (~/.claude/.credentials.json missing in WSL)."

    oauth, key = _find_oauth_block(creds)
    if not oauth:
        return f"❌ Credentials file found but no OAuth block. Keys: {list(creds)}"

    exp = _expires_at_seconds(oauth)
    remaining = exp - time.time() if exp else None
    has_refresh = bool(oauth.get("refreshToken"))
    has_access  = bool(oauth.get("accessToken"))

    lines = [
        f"OAuth key: <code>{key}</code>",
        f"Has access token: {has_access}",
        f"Has refresh token: {has_refresh}",
    ]
    if exp:
        if remaining and remaining > 0:
            lines.append(f"Token expires in: {int(remaining)}s ({int(remaining)//60} min)")
        else:
            lines.append(f"Token EXPIRED {abs(int(remaining or 0))}s ago")
    else:
        lines.append("expiresAt: not set")

    return "\n".join(lines)
