import json
from pathlib import Path

import requests


UPLOAD_TIMEOUT_FLOOR_S = 30.0


class MoonrakerError(Exception):
    pass


class MoonrakerTimeout(MoonrakerError):
    pass


class MoonrakerClient:
    def __init__(
        self,
        base_url,
        timeout=5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()

    def _get(
        self,
        endpoint,
        params=None,
    ):
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
            )

            response.raise_for_status()

        except requests.Timeout as error:
            raise MoonrakerTimeout(
                f"Moonraker request timed out: {error}"
            ) from error

        except requests.RequestException as error:
            raise MoonrakerError(
                f"Moonraker request failed: {error}"
            ) from error

        try:
            payload = response.json()

        except ValueError as error:
            raise MoonrakerError(
                "Moonraker returned invalid JSON"
            ) from error

        if "error" in payload:
            raise MoonrakerError(
                f"Moonraker API error: "
                f"{payload['error']}"
            )

        return payload.get("result")


    def _post(
        self,
        endpoint,
        *,
        json_payload=None,
        data=None,
        files=None,
        timeout=None,
    ):
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.post(
                url,
                json=json_payload,
                data=data,
                files=files,
                timeout=self.timeout if timeout is None else timeout,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise MoonrakerTimeout(f"Moonraker request timed out: {error}") from error
        except requests.RequestException as error:
            raise MoonrakerError(f"Moonraker request failed: {error}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise MoonrakerError("Moonraker returned invalid JSON") from error

        if "error" in payload:
            raise MoonrakerError(f"Moonraker API error: {payload['error']}")

        return payload.get("result")

    def get_server_info(self):
        return self._get(
            "/server/info"
        )

    def get_analysis_status(self):
        return self._get("/server/analysis/status")

    def estimate_analysis(self, filename, estimator_config=None):
        payload = {"filename": filename}
        if estimator_config:
            payload["estimator_config"] = estimator_config
        return self._post("/server/analysis/estimate", json_payload=payload)

    def query_objects(
        self,
        objects,
    ):
        params = {}

        for object_name, fields in objects.items():
            if fields is None:
                params[object_name] = None

            else:
                params[object_name] = ",".join(fields)

        result = self._get(
            "/printer/objects/query",
            params=params,
        )

        if not isinstance(result, dict):
            raise MoonrakerError(
                "Invalid object query response"
            )

        status = result.get("status")

        if not isinstance(status, dict):
            raise MoonrakerError(
                "Moonraker response contains no status"
            )

        return status

    def send_gcode(
        self,
        script,
        timeout=None,
    ):
        try:
            return self._post(
                "/printer/gcode/script",
                json_payload={"script": script},
                timeout=timeout,
            )
        except MoonrakerTimeout as error:
            raise MoonrakerTimeout(f"G-code request timed out: {error}") from error
        except MoonrakerError as error:
            raise MoonrakerError(f"G-code request failed: {error}") from error

    def upload_file(self, *, local_path, remote_dir="klipper-cnc-assistant", checksum=None, print_file=False):
        file_path = Path(local_path)
        data = {"root": "gcodes", "path": remote_dir, "print": "true" if print_file else "false"}
        if checksum:
            data["checksum"] = checksum
        upload_timeout = max(float(self.timeout), UPLOAD_TIMEOUT_FLOOR_S)
        try:
            with file_path.open("rb") as handle:
                response = self.session.post(
                    f"{self.base_url}/server/files/upload",
                    data=data,
                    files={"file": (file_path.name, handle, "text/plain")},
                    timeout=upload_timeout,
                )
            content_type = response.headers.get("Content-Type", "")
            body_text = response.text
            if response.status_code != 201:
                raise MoonrakerError(
                    "Moonraker upload failed: "
                    f"HTTP {response.status_code} "
                    f"content_type={content_type!r} "
                    f"url={response.url!s} "
                    f"local_name={file_path.name!r} "
                    f"remote_dir={remote_dir!r} "
                    f"body={body_text[:1000]!r}"
                )
            payload = response.json()
        except requests.Timeout as error:
            raise MoonrakerTimeout(
                "Moonraker upload timed out after "
                f"{upload_timeout:.1f}s; the server outcome is uncertain and no automatic retry was attempted. "
                f"local_name={file_path.name!r} remote_dir={remote_dir!r}"
            ) from error
        except requests.RequestException as error:
            raise MoonrakerError(f"Moonraker upload failed: {error}") from error
        except ValueError as error:
            raise MoonrakerError(
                "Moonraker upload returned invalid JSON: "
                f"url={self.base_url}/server/files/upload "
                f"local_name={file_path.name!r} "
                f"remote_dir={remote_dir!r}"
            ) from error
        if "error" in payload:
            raise MoonrakerError(f"Moonraker API error: {payload['error']}")
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            result = payload["result"]
        elif isinstance(payload, dict):
            result = payload
        else:
            raise MoonrakerError(
                "Moonraker upload returned an unsupported payload: "
                f"url={self.base_url}/server/files/upload "
                f"local_name={file_path.name!r} "
                f"remote_dir={remote_dir!r} "
                f"payload_type={type(payload).__name__}"
            )
        item = result.get("item")
        if not isinstance(item, dict) or item.get("root") != "gcodes" or not isinstance(item.get("path"), str) or not item["path"].strip():
            raise MoonrakerError(
                "Moonraker upload returned no valid gcodes item.path: "
                f"url={self.base_url}/server/files/upload "
                f"local_name={file_path.name!r} "
                f"remote_dir={remote_dir!r} "
                f"payload={json.dumps(result, ensure_ascii=True)[:1000]}"
            )
        if print_file and not result.get("print_started") and not result.get("print_queued"):
            raise MoonrakerError(
                "START_NOT_ACCEPTED: Moonraker did not accept the uploaded file for printing. "
                f"url={self.base_url}/server/files/upload "
                f"local_name={file_path.name!r} "
                f"remote_dir={remote_dir!r} "
                f"payload={json.dumps(result, ensure_ascii=True)[:1000]}"
            )
        return result

    def start_print(self, filename):
        return self._post("/printer/print/start", json_payload={"filename": filename})

    def pause_print(self):
        return self._post("/printer/print/pause")

    def resume_print(self):
        return self._post("/printer/print/resume")

    def cancel_print(self):
        return self._post("/printer/print/cancel")
