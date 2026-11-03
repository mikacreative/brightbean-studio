"""Bridge a local stdio MCP client to SocialOS without exposing its token in config."""

from __future__ import annotations

import ipaddress
import json
import os
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _configuration():
    project_dir = Path(__file__).resolve().parent.parent
    token_path = Path(os.environ.get("SOCIALOS_MCP_TOKEN_FILE", project_dir / ".socialos-mcp-token"))
    url = os.environ.get("SOCIALOS_MCP_URL", "http://127.0.0.1:8000/api/v1/mcp")
    parsed = urlsplit(url)
    try:
        is_loopback = bool(parsed.hostname and ipaddress.ip_address(parsed.hostname).is_loopback)
    except ValueError:
        is_loopback = False
    if parsed.scheme != "http" or not is_loopback or parsed.username or parsed.password:
        raise ValueError("The MCP destination must be a numeric local loopback HTTP URL.")
    if not token_path.is_file() or stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise ValueError("The MCP token file must exist and be readable only by its owner (mode 600).")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token.startswith("bb_studio_") or "\n" in token:
        raise ValueError("The MCP token file does not contain a valid SocialOS token.")
    return url, token


def _error(message_id, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": message}}


def main():
    try:
        url, token = _configuration()
    except (OSError, ValueError) as exc:
        print(f"SocialOS MCP: {exc}", file=sys.stderr)
        return 1
    opener = build_opener(_NoRedirect())
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, "Invalid JSON from the MCP client.")), flush=True)
            continue
        request = Request(
            url,
            data=json.dumps(message).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "SocialOS-local-MCP-bridge/1.0",
            },
            method="POST",
        )
        try:
            with opener.open(request, timeout=60) as response:
                body = response.read()
            if body:
                result = json.loads(body)
                print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
        except (HTTPError, URLError, OSError, ValueError):
            # Never print the request, Authorization header, or server response.
            if isinstance(message, dict) and "id" in message:
                print(
                    json.dumps(_error(message["id"], "Could not connect to the local SocialOS MCP server.")), flush=True
                )
            else:
                print("SocialOS MCP: local request failed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
