"""The local Codex bridge keeps the bearer secret off the MCP wire output."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "scripts" / "socialos_mcp_bridge.py"


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("bb_studio_synthetic_test_token", encoding="utf-8")
    path.chmod(0o600)
    return path


def run_bridge(token_file, url, input_text=""):
    env = {**os.environ, "SOCIALOS_MCP_TOKEN_FILE": str(token_file), "SOCIALOS_MCP_URL": url}
    return subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
        check=False,
    )


def test_bridge_relays_requests_without_emitting_the_token(token_file):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.headers.get("Authorization"), data))
            if "id" not in data:
                self.send_response(202)
                self.end_headers()
                return
            body = json.dumps({"jsonrpc": "2.0", "id": data["id"], "result": {"ok": True}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = run_bridge(
            token_file,
            f"http://127.0.0.1:{server.server_port}/api/v1/mcp",
            "".join(json.dumps(m) + "\n" for m in messages),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.returncode == 0, result.stderr
    assert [json.loads(line)["id"] for line in result.stdout.splitlines()] == [1, 2]
    assert len(seen) == 3
    assert all(auth == "Bearer bb_studio_synthetic_test_token" for auth, _ in seen)
    assert "bb_studio_synthetic_test_token" not in result.stdout + result.stderr


def test_bridge_rejects_nonlocal_destinations(token_file):
    result = run_bridge(token_file, "https://example.com/api/v1/mcp")
    assert result.returncode != 0
    assert "local" in result.stderr.lower()


def test_bridge_rejects_a_token_file_readable_by_other_users(token_file):
    token_file.chmod(0o644)
    result = run_bridge(token_file, "http://127.0.0.1:8000/api/v1/mcp")
    assert result.returncode != 0
    assert "bb_studio_synthetic_test_token" not in result.stdout + result.stderr
