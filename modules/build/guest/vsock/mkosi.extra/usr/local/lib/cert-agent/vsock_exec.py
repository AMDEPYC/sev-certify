#!/usr/bin/env python3
"""
Reference vsock command agent for mkosi test guest images.

Install to e.g. ``/usr/local/lib/cert-agent/vsock_exec.py`` and enable via systemd.
Listens on AF_VSOCK port 5000 (configurable with ``--port``).

Protocol (single JSON object per connection, connection closed after response):

  Request:  {"ping": true}
  Response: {"ok": true}

  Request:  {"cmd": "echo hello"}
  Response: {"exit_code": 0, "stdout": "hello\\n", "stderr": ""}
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys

AF_VSOCK = getattr(socket, "AF_VSOCK", 40)
VMADDR_CID_ANY = -1
DEFAULT_PORT = 5000
MAX_REQUEST_BYTES = 65536


def _handle_request(payload: dict) -> dict:
    if payload.get("ping") is True:
        return {"ok": True}
    command = payload.get("cmd")
    if not isinstance(command, str) or not command.strip():
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "missing or empty cmd",
            "error": "invalid request",
        }
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        check=False
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _serve_client(conn: socket.socket) -> None:
    try:
        data = conn.recv(MAX_REQUEST_BYTES)
        if not data:
            return
        payload = json.loads(data.decode("utf-8"))
        response = _handle_request(payload)
    except json.JSONDecodeError:
        response = {
            "exit_code": 1,
            "stdout": "",
            "stderr": "invalid JSON request",
            "error": "invalid request",
        }
    except Exception as exc:  # noqa: BLE001 - agent must always respond
        response = {
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "error": "agent failure",
        }
    conn.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Cert test guest vsock exec agent")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    server.bind((VMADDR_CID_ANY, args.port))
    server.listen(8)

    while True:
        conn, _addr = server.accept()
        with conn:
            _serve_client(conn)


if __name__ == "__main__":
    sys.exit(main())
