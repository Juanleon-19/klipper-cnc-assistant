from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import requests

from klipper_cnc_assistant.moonraker.client import (
    UPLOAD_TIMEOUT_FLOOR_S,
    MoonrakerClient,
    MoonrakerError,
    MoonrakerTimeout,
)


class _CaptureServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, *, status: int, payload: object, headers: dict[str, str] | None = None):
        super().__init__(server_address, RequestHandlerClass)
        self.response_status = status
        self.response_payload = payload
        self.response_headers = headers or {"Content-Type": "application/json"}
        self.request_count = 0
        self.last_request: dict[str, object] | None = None


class _CaptureHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.request_count += 1  # type: ignore[attr-defined]
        self.server.last_request = {  # type: ignore[attr-defined]
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        }
        payload = self.server.response_payload  # type: ignore[attr-defined]
        raw = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
        self.send_response(self.server.response_status)  # type: ignore[attr-defined]
        for key, value in self.server.response_headers.items():  # type: ignore[attr-defined]
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class MoonrakerClientUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.local_file = Path(self.tempdir.name) / "sample_compensated.gcode"
        self.local_file.write_text("G21\nG90\nM400\n", encoding="utf-8")

    def _start_server(self, payload: object, *, status: int = 201, headers: dict[str, str] | None = None) -> _CaptureServer:
        server = _CaptureServer(("127.0.0.1", 0), _CaptureHandler, status=status, payload=payload, headers=headers)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        return server

    def test_upload_accepts_native_direct_response_without_result(self) -> None:
        payload = {
            "item": {
                "path": "klipper-cnc-assistant/proj/setup/superior/sample_compensated.gcode",
                "root": "gcodes",
                "modified": 0,
                "size": 123,
                "permissions": "rw",
            },
            "print_started": True,
            "print_queued": False,
            "action": "create_file",
        }
        server = self._start_server(payload)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        result = client.upload_file(
            local_path=self.local_file,
            remote_dir="klipper-cnc-assistant/proj/setup/superior",
            checksum="abc123",
            print_file=True,
        )

        self.assertEqual(result["item"]["path"], payload["item"]["path"])
        self.assertTrue(result["print_started"])
        self.assertEqual(server.request_count, 1)
        request = server.last_request
        assert request is not None
        self.assertEqual(request["path"], "/server/files/upload")
        headers = request["headers"]
        self.assertIn("multipart/form-data", headers.get("Content-Type", ""))
        body = request["body"]
        assert isinstance(body, bytes)
        self.assertIn(b'name="root"', body)
        self.assertIn(b'gcodes', body)
        self.assertIn(b'name="path"', body)
        self.assertIn(b'klipper-cnc-assistant/proj/setup/superior', body)
        self.assertIn(b'name="checksum"', body)
        self.assertIn(b'abc123', body)
        self.assertIn(b'name="print"', body)
        self.assertIn(b'true', body)
        self.assertIn(b'name="file"; filename="sample_compensated.gcode"', body)

    def test_upload_accepts_wrapped_result_for_compatibility(self) -> None:
        payload = {
            "result": {
                "item": {"path": "klipper-cnc-assistant/proj/file.gcode", "root": "gcodes"},
                "print_started": True,
                "print_queued": False,
                "action": "create_file",
            }
        }
        server = self._start_server(payload)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        result = client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

        self.assertEqual(result["item"]["path"], "klipper-cnc-assistant/proj/file.gcode")

    def test_upload_uses_longer_timeout_floor_than_short_request_timeout(self) -> None:
        payload = {
            "item": {"path": "klipper-cnc-assistant/proj/file.gcode", "root": "gcodes"},
            "print_started": True,
            "print_queued": False,
        }
        response = Mock()
        response.status_code = 201
        response.headers = {"Content-Type": "application/json"}
        response.text = json.dumps(payload)
        response.json.return_value = payload
        client = MoonrakerClient("http://moonraker.local", timeout=2.0)
        client.session.post = Mock(return_value=response)

        client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

        self.assertEqual(client.session.post.call_count, 1)
        self.assertEqual(client.session.post.call_args.kwargs["timeout"], UPLOAD_TIMEOUT_FLOOR_S)
        self.assertGreater(UPLOAD_TIMEOUT_FLOOR_S, client.timeout)

    def test_upload_timeout_is_not_retried_automatically(self) -> None:
        client = MoonrakerClient("http://moonraker.local", timeout=2.0)
        client.session.post = Mock(side_effect=requests.Timeout("slow upload"))

        with self.assertRaisesRegex(MoonrakerTimeout, "outcome is uncertain and no automatic retry"):
            client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

        self.assertEqual(client.session.post.call_count, 1)

    def test_upload_requires_http_201(self) -> None:
        payload = {
            "item": {"path": "klipper-cnc-assistant/proj/file.gcode", "root": "gcodes"},
            "print_started": True,
            "print_queued": False,
        }
        server = self._start_server(payload, status=200)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        with self.assertRaisesRegex(MoonrakerError, "HTTP 200"):
            client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

    def test_upload_fails_without_item(self) -> None:
        payload = {"print_started": True, "print_queued": False, "action": "create_file"}
        server = self._start_server(payload)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        with self.assertRaisesRegex(MoonrakerError, "no valid gcodes item.path"):
            client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

    def test_upload_fails_without_path(self) -> None:
        payload = {
            "item": {"root": "gcodes"},
            "print_started": True,
            "print_queued": False,
            "action": "create_file",
        }
        server = self._start_server(payload)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        with self.assertRaisesRegex(MoonrakerError, "no valid gcodes item.path"):
            client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)

    def test_upload_fails_when_start_not_accepted(self) -> None:
        payload = {
            "item": {"path": "klipper-cnc-assistant/proj/file.gcode", "root": "gcodes"},
            "print_started": False,
            "print_queued": False,
            "action": "create_file",
        }
        server = self._start_server(payload)
        client = MoonrakerClient(f"http://127.0.0.1:{server.server_address[1]}")

        with self.assertRaisesRegex(MoonrakerError, "START_NOT_ACCEPTED"):
            client.upload_file(local_path=self.local_file, remote_dir="klipper-cnc-assistant/proj", print_file=True)


if __name__ == "__main__":
    unittest.main()
