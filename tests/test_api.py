from __future__ import annotations

import json
import tempfile
import unittest

from klipper_cnc_assistant.machine.config import MachineMode
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from tests.test_machine_runtime import DummyConnectClient, FailingArduinoDriver, IdleTelemetry, config
from klipper_cnc_assistant.machine.runtime import MachineRuntime
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name)
        self.app = create_app(data_dir=self.data_dir)
        self.client = TestClient(self.app)

    def _create_project(self) -> str:
        return self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto API",
                "material": {"ancho_mm": 80.0, "alto_mm": 50.0, "espesor_mm": 1.6},
            },
        ).json()["id"]

    def _create_operation(self, project_id: str) -> str:
        return self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "nombre": "Aislamiento",
                "tipo": "aislamiento",
                "cara": "superior",
                "orden": 0,
                "herramienta": "V-bit 30",
            },
        ).json()["id"]

    def _write_setup_preparation(self, project_id: str, preparation: dict) -> None:
        project_file = self.data_dir / "projects" / project_id / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload["montajes"][0]["preparacion"] = preparation
        project_file.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["estado"], "ok")

    def test_create_project_and_list_projects(self) -> None:
        project_id = self._create_project()
        list_response = self.client.get("/api/projects")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["id"], project_id)

    def test_operations_and_analysis_endpoints(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        upload_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={
                "nombre_archivo": "job.nc",
                "contenido": "G21\nG90\nG1 X10 Y10 F120\nM3\nT1\n",
            },
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertTrue(upload_response.json()["archivo_gcode"].startswith("originals/"))

        analyze_response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")
        self.assertEqual(analyze_response.status_code, 200)
        payload = analyze_response.json()
        self.assertTrue(payload["cabe_en_material"])
        self.assertEqual(payload["acciones_husillo"], ["M3"])
        self.assertEqual(payload["cambios_herramienta"], ["T1"])
        self.assertEqual(payload["analysis_version"], payload["current_analysis_version"])
        self.assertFalse(payload["analisis_desactualizado"])


    def test_project_list_omits_heavy_operation_analysis_but_detail_keeps_it(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={
                "nombre_archivo": "job.nc",
                "contenido": "G21\nG90\nG1 X10 Y10 F120\nM3\nT1\n",
            },
        )
        self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")

        list_payload = self.client.get("/api/projects").json()
        listed_operation = list_payload[0]["operaciones"][0]
        self.assertIsNone(listed_operation["analisis"])

        detail_payload = self.client.get(f"/api/projects/{project_id}").json()
        detail_analysis = detail_payload["operaciones"][0]["analisis"]
        self.assertIsNotNone(detail_analysis)
        self.assertIn("segmentos_lineales", detail_analysis)


    def test_get_job_run_is_read_only_and_returns_404_without_creating_run(self) -> None:
        project_id = self._create_project()
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]
        run_path = self.data_dir / "projects" / project_id / "reports" / "jobs" / setup_id / "superior" / "current_run.json"

        response = self.client.get(f"/api/projects/{project_id}/job-run?setup_id={setup_id}&face=superior")

        self.assertEqual(response.status_code, 404)
        self.assertIn("No existe una ejecución activa", response.json()["detalle"])
        self.assertFalse(run_path.exists())

    def test_execution_preflight_reports_concrete_blockers_without_hardware(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/execution/preflight")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "PREFLIGHT")
        self.assertFalse(payload["ready"])
        checks = {item["name"]: item for item in payload["checks"]}
        self.assertIn("modo_fisico", checks)
        self.assertIn("mapa_medido", checks)
        self.assertIn("archivo_compensado", checks)
        self.assertFalse(checks["modo_fisico"]["ok"])
        self.assertIn("modo físico", checks["modo_fisico"]["detail"])

    def test_delete_operation_endpoint(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        delete_response = self.client.delete(f"/api/projects/{project_id}/operations/{operation_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["detalle"], "Operacion eliminada.")

    def test_http_errors_are_returned_in_spanish(self) -> None:
        response = self.client.get("/api/projects/no-existe")
        self.assertEqual(response.status_code, 404)
        self.assertIn("El proyecto", response.json()["detalle"])

        invalid_response = self.client.post(
            "/api/projects",
            json={"nombre": "", "material": {"ancho_mm": -1, "alto_mm": 10.0}},
        )
        self.assertEqual(invalid_response.status_code, 422)
        self.assertIn("Solicitud invalida", invalid_response.json()["detalle"])

    def test_reference_validation_errors_are_translated(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        )
        response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/reference-session/z-reference",
            json={"x_mm": 0, "y_mm": 0, "z_mm": "abc"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("z_mm", response.json()["detalle"])
        self.assertIn("numero valido", response.json()["detalle"])

    def test_reference_session_returns_structured_captured_position_xy(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._write_setup_preparation(project_id, {
            "origen_trabajo": {
                "x_mm": 60.0, "y_mm": 88.75, "z_mm": None,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75},
            }
        })

        response = self.client.get(f"/api/projects/{project_id}/operations/{operation_id}/reference-session")

        self.assertEqual(response.status_code, 200)
        captured = response.json()["origen_trabajo"]["posicion_captura"]
        self.assertEqual(captured, {"x_mm": 60.0, "y_mm": 88.75, "z_mm": None})

    def test_reference_session_returns_structured_captured_position_xyz(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._write_setup_preparation(project_id, {
            "referencia_z": {
                "x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05},
            }
        })

        response = self.client.get(f"/api/projects/{project_id}/operations/{operation_id}/reference-session")

        self.assertEqual(response.status_code, 200)
        captured = response.json()["referencia_z"]["posicion_captura"]
        self.assertEqual(captured, {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05})

    def test_reference_session_accepts_absent_and_legacy_captured_position(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._write_setup_preparation(project_id, {
            "origen_trabajo": {
                "x_mm": 12.0, "y_mm": -3.5, "z_mm": None,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
            },
            "referencia_z": {
                "x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
                "posicion_captura": "60.0,88.75,10.05",
            },
        })

        response = self.client.get(f"/api/projects/{project_id}/operations/{operation_id}/reference-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["origen_trabajo"]["posicion_captura"])
        self.assertEqual(payload["referencia_z"]["posicion_captura"], {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05})
        self.assertEqual(payload["referencia_z"]["z_mm"], 10.05)

    def test_reference_session_response_keeps_captured_reference_without_new_probe(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._write_setup_preparation(project_id, {
            "origen_trabajo": {
                "x_mm": 60.0, "y_mm": 88.75, "z_mm": None,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
                "maquina": "klipper", "homed_axes": "xyz", "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05},
            },
            "referencia_z": {
                "x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05,
                "fuente": "MEASURED", "fecha": "2026-07-14T10:00:00+00:00",
                "maquina": "klipper", "homed_axes": "xyz", "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05},
            },
        })

        response = self.client.get(f"/api/projects/{project_id}/operations/{operation_id}/reference-session")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["origen_trabajo"]["posicion_captura"]["x_mm"], 60.0)
        self.assertEqual(payload["origen_trabajo"]["posicion_captura"]["z_mm"], 10.05)
        self.assertEqual(payload["referencia_z"]["fuente"], "MEASURED")
        self.assertEqual(payload["referencia_z"]["sesion"], "physical-session")


    def test_go_to_reference_point_endpoint_uses_saved_reference_coordinates(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        class ReferenceServiceStub:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get_saved_reference_point(self, project: str, operation: str) -> dict[str, float]:
                self.calls.append((project, operation))
                return {"reference_x": 60.0, "reference_y": 88.75}

        class RuntimeStub:
            def __init__(self) -> None:
                self.calls: list[dict[str, float]] = []

            def go_to_reference_point(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "accepted": True,
                    "reference_x": kwargs["reference_x"],
                    "reference_y": kwargs["reference_y"],
                    "preparation_z": 115.0,
                    "final_state": "REFERENCE_MOVE_COMPLETE",
                    "message": "Máquina ubicada en el punto de referencia.",
                }

        reference_service = ReferenceServiceStub()
        runtime = RuntimeStub()
        self.app.state.reference_session_service = reference_service
        self.app.state.machine_runtime = runtime

        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference/go-to")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(reference_service.calls, [(project_id, operation_id)])
        self.assertEqual(runtime.calls, [{"reference_x": 60.0, "reference_y": 88.75}])
        self.assertEqual(response.json()["final_state"], "REFERENCE_MOVE_COMPLETE")
        self.assertEqual(response.json()["reference_x"], 60.0)
        self.assertEqual(response.json()["reference_y"], 88.75)

    def test_machine_connect_returns_structured_arduino_error_instead_of_500(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 10),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 100),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime = MachineRuntime(
            config(MachineMode.PHYSICAL, moonraker_url="http://moonraker", moonraker_ws="ws://moonraker/websocket", serial_port="/dev/ttyUSB-arduino"),
            client_factory=lambda _url, timeout=None: DummyConnectClient(machine),
            telemetry_factory=lambda *_args, **_kwargs: IdleTelemetry(),
            serial_factory=lambda **kwargs: FailingArduinoDriver(**kwargs),
            discovery=lambda _client: machine,
        )
        self.app.state.machine_runtime = runtime

        response = self.client.post("/api/machine/connect")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["detail"]["component"], "arduino")
        self.assertEqual(payload["detail"]["status"], "connection_failed")
        self.assertTrue(payload["detail"]["retryable"])
        self.assertEqual(payload["detail"]["port"], "/dev/ttyUSB-arduino")
        self.assertEqual(payload["runtime"]["connection"]["status"], "partial")
        self.assertTrue(payload["runtime"]["moonraker"]["http_connected"])
        self.assertTrue(payload["runtime"]["klipper"]["ready"])
        runtime.stop()

    def test_machine_session_is_simulated_and_home_is_unknown_until_confirmed(self) -> None:
        response = self.client.get("/api/machine/session")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["estado"], "simulada_lista_para_preparacion")
        self.assertFalse(payload["home_realizado"])
        self.assertIsNone(payload["referencia_maquina_confirmada_en"])

    def test_simulated_home_is_confirmed_once_per_session(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        first = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        second = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["machine_reference"]["fecha"], second.json()["machine_reference"]["fecha"])

    def test_old_analysis_is_detected_as_stale(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={"nombre_archivo": "job.nc", "contenido": "G21\nG94\nG1 X5 Y5 F120\n"},
        )
        self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")

        project_file = self.data_dir / "projects" / project_id / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        del payload["operaciones"][0]["analisis"]["analysis_version"]
        project_file.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

        stale_client = TestClient(create_app(data_dir=self.data_dir))
        try:
            response = stale_client.get(f"/api/projects/{project_id}")
            self.assertEqual(response.status_code, 200)
            analysis = response.json()["operaciones"][0]["analisis"]
            self.assertTrue(analysis["analisis_desactualizado"])
        finally:
            stale_client.close()

    def test_api_paths_do_not_touch_hardware(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        with patch(
            "klipper_cnc_assistant.moonraker.client.MoonrakerClient.__init__",
            side_effect=AssertionError("No debe tocar Moonraker."),
        ), patch(
            "klipper_cnc_assistant.input.serial_driver.SerialDriver.__init__",
            side_effect=AssertionError("No debe tocar serial."),
        ):
            app = create_app(data_dir=self.data_dir)
            client = TestClient(app)
            response = client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
            self.assertEqual(response.status_code, 200)
            client.close()


    def test_setup_operations_api_and_shared_references(self) -> None:
        project_id = self._create_project()
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]
        first = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "setup_id": setup_id, "nombre": "Taladrado 0,8 mm",
                "tipo": "taladrado", "herramienta": "Broca 0,8 mm",
            },
        ).json()
        second = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "setup_id": setup_id, "nombre": "Taladrado 1,0 mm",
                "tipo": "taladrado", "herramienta": "Broca 1,0 mm",
            },
        ).json()
        self.assertEqual(first["setup_id"], setup_id)
        self.assertEqual(second["orden"], 1)

        self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/reference-session/machine-reference"
        )
        self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        )
        shared = self.client.get(
            f"/api/projects/{project_id}/operations/{second['id']}/reference-session"
        ).json()
        self.assertEqual(shared["origen_trabajo"]["x_mm"], 0)
        self.assertEqual(shared["origen_trabajo"]["y_mm"], 0)

        simulated = self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/height-map/simulate",
            json={
                "filas": 3,
                "columnas": 3,
                "superficie_simulada": "inclinada",
                "repeticion_simulacion": 4,
                "probe_region": {
                    "min_x_mm": 2, "min_y_mm": 2,
                    "max_x_mm": 78, "max_y_mm": 48,
                },
                "exclusion_zones": [],
            },
        )
        self.assertEqual(simulated.status_code, 200)
        shared_map = self.client.get(
            f"/api/projects/{project_id}/operations/{second['id']}/height-map"
        )
        self.assertEqual(shared_map.status_code, 200)
        self.assertEqual(shared_map.json()["version"], simulated.json()["version"])

    def test_reset_setup_preparation_serializes_machine_session_without_crash(self) -> None:
        project_id = self._create_project()
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]

        response = self.client.post(
            f"/api/projects/{project_id}/setups/{setup_id}/reset-preparation",
            json={},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("machine_session", payload)
        self.assertIn("estado", payload["machine_session"])
        self.assertIn("runtime", payload)

    def test_system_info_exposes_build_compatibility(self) -> None:
        payload = self.client.get("/api/system/info").json()
        self.assertIn("backend_version", payload)
        self.assertIn("frontend_build", payload)
        self.assertIn("git_commit", payload)
        self.assertEqual(payload["schema_version"], "1.6")
