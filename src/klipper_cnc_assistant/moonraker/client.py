from pathlib import Path

import requests


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

    def upload_file(self, *, local_path, remote_dir="klipper-cnc-assistant"):
        file_path = Path(local_path)
        with file_path.open("rb") as handle:
            result = self._post(
                "/server/files/upload",
                data={"root": "gcodes", "path": remote_dir},
                files={"file": (file_path.name, handle, "text/plain")},
            )
        if isinstance(result, dict):
            result.setdefault("path", f"{remote_dir}/{file_path.name}")
            result.setdefault("filename", file_path.name)
        return result

    def start_print(self, filename):
        return self._post("/printer/print/start", json_payload={"filename": filename})

    def pause_print(self):
        return self._post("/printer/print/pause")

    def resume_print(self):
        return self._post("/printer/print/resume")

    def cancel_print(self):
        return self._post("/printer/print/cancel")
