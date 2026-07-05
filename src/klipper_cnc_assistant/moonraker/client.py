import requests


class MoonrakerError(Exception):
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
    ):
        url = (
            f"{self.base_url}"
            "/printer/gcode/script"
        )

        try:
            response = self.session.post(
                url,
                json={
                    "script": script,
                },
                timeout=self.timeout,
            )

            response.raise_for_status()

        except requests.RequestException as error:
            raise MoonrakerError(
                f"G-code request failed: {error}"
            ) from error

        try:
            payload = response.json()

        except ValueError as error:
            raise MoonrakerError(
                "Moonraker returned invalid JSON"
            ) from error

        if "error" in payload:
            raise MoonrakerError(
                f"Moonraker G-code error: "
                f"{payload['error']}"
            )

        return payload.get("result")
