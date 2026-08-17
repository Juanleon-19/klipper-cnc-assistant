from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig


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

    def _upload_and_analyze_operation(self, project_id: str, operation_id: str, *, content: str = "G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.050 F120\n") -> None:
        upload = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={"nombre_archivo": "job.nc", "contenido": content},
        )
        self.assertEqual(upload.status_code, 200)
        analyze = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")
        self.assertEqual(analyze.status_code, 200)

    def _write_setup_preparation(self, project_id: str, preparation: dict) -> None:
        project_file = self.data_dir / "projects" / project_id / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload["montajes"][0]["preparacion"] = preparation
        project_file.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def test_reconnect_arduino_endpoint_returns_runtime_snapshot(self) -> None:
        runtime = self.app.state.machine_runtime
        runtime.reconnect_arduino = lambda: runtime.snapshot()

        response = self.client.post("/api/machine/reconnect-arduino")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], runtime.snapshot()["state"])

    def test_physical_work_origin_capture_is_idempotent_for_same_observation(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        observation = {
            "position": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 10.05},
            "machine_label": "http://moonraker.local",
            "homed_axes": "xyz",
            "session_id": "2026-07-30T00:00:00+00:00#serial-2",
        }
        self.app.state.machine_runtime.capture_reference_observation = lambda: observation

        first = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/physical-work-origin")
        second = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/physical-work-origin")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["origen_trabajo"], second.json()["origen_trabajo"])
        self.assertEqual(first.json()["origen_trabajo"]["fecha"], second.json()["origen_trabajo"]["fecha"])

    def test_physical_z_reference_from_probe_uses_active_probe_observation(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.app.state.machine_runtime.capture_probe_reference_observation = lambda: {
            "position": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
            "machine_label": "http://moonraker.local",
            "homed_axes": "xyz",
            "session_id": "2026-07-30T00:00:00+00:00#serial-3",
        }

        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/physical-z-reference-from-probe")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["referencia_z"]
        self.assertEqual(payload["x_mm"], 60.0)
        self.assertEqual(payload["y_mm"], 88.75)
        self.assertEqual(payload["z_mm"], 0.015)
        self.assertEqual(payload["sesion"], "2026-07-30T00:00:00+00:00#serial-3")
        self.assertEqual(payload["fuente"], "MEASURED")

    def test_job_run_start_conflict_returns_structured_detail(self) -> None:
        def raise_conflict(*_args, **_kwargs):
            raise ApplicationError("JOB_ACTIVE_CONFLICT")

        self.app.state.job_service.start_run = raise_conflict
        self.app.state.job_service.describe_run_conflict = lambda **_kwargs: {
            "code": "JOB_ACTIVE_CONFLICT",
            "message": "Ya existe un trabajo activo para este montaje y cara.",
            "conflict_condition": "current_run.state=JOB_VALIDATING no es terminal ni JOB_READY.",
            "existing_run": {"run_id": "job-run/setup-main/superior/20260722-040230", "status": "JOB_VALIDATING"},
            "moonraker": {"print_state": "standby", "is_active": False},
            "available_actions": ["open", "archive-stale"],
            "can_archive_stale": True,
        }

        response = self.client.post('/api/projects/proj_1/job-run/start', json={"setup_id": "setup-main", "face": "superior"})

        self.assertEqual(response.status_code, 409)
        payload = response.json()["detail"]
        self.assertEqual(payload["code"], "JOB_ACTIVE_CONFLICT")
        self.assertEqual(payload["existing_run"]["run_id"], "job-run/setup-main/superior/20260722-040230")
        self.assertTrue(payload["can_archive_stale"])

    def test_archive_stale_job_run_endpoint_returns_archive_report(self) -> None:
        self.app.state.job_service.archive_stale_run = lambda **_kwargs: {
            "archived_run_id": "job-run/setup-main/superior/20260722-040230",
            "previous_status": "WAITING_FOR_KLIPPER",
            "archive_path": "reports/jobs/setup-main/superior/history/stale.json",
            "locks_released": ["job_run.current_run"],
            "can_start_new_run": True,
        }

        response = self.client.post('/api/projects/proj_1/job-run/archive-stale', json={"setup_id": "setup-main", "face": "superior"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["archived_run_id"], "job-run/setup-main/superior/20260722-040230")
        self.assertTrue(response.json()["can_start_new_run"])

    def test_go_to_reference_rejects_missing_saved_reference_without_motion(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference/go-to")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detalle"], "No hay un punto de referencia guardado.")

    def test_go_to_reference_does_not_use_operation_tool_change_profile(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        operation = self.client.patch(
            f"/api/projects/{project_id}/operations/{operation_id}",
            json={"nombre": "Aislamiento", "herramienta": "Broca 0.8 mm", "tool_reference_profile": "long_tool"},
        ).json()
        self._write_setup_preparation(project_id, {
            "referencia_z": {
                "x_mm": 60.0,
                "y_mm": 88.75,
                "z_mm": 0.015,
                "fuente": "MEASURED",
                "fecha": "2026-08-17T00:00:00+00:00",
                "maquina": "klipper",
                "homed_axes": "xyz",
                "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
            },
        })
        calls: list[dict[str, object]] = []

        def record_reference_move(**payload: object) -> dict[str, object]:
            calls.append(payload)
            return {"accepted": True, **payload}

        self.app.state.machine_runtime.go_to_reference_point = record_reference_move

        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference/go-to")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(operation["tool_reference_profile"], "long_tool")
        self.assertEqual(calls, [{"reference_x": 60.0, "reference_y": 88.75}])

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

        operation = self.client.get(f"/api/projects/{project_id}").json()["operaciones"][0]
        self.assertEqual(operation["tool_reference_profile"], "standard")
        updated = self.client.patch(
            f"/api/projects/{project_id}/operations/{operation_id}",
            json={
                "nombre": operation["nombre"],
                "tool_id": operation["tool_id"],
                "herramienta": operation["herramienta"],
                "tool_reference_profile": "long_tool",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["tool_reference_profile"], "long_tool")

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

    def test_plan_from_reference_uses_saved_reference_without_new_capture_and_persists_exact_parameters(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._upload_and_analyze_operation(project_id, operation_id)
        self._write_setup_preparation(project_id, {
            "origen_trabajo": {
                "x_mm": 60.0,
                "y_mm": 88.75,
                "z_mm": None,
                "fuente": "MEASURED",
                "fecha": "2026-07-31T00:00:00+00:00",
                "maquina": "klipper",
                "homed_axes": "xyz",
                "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": None},
            },
            "referencia_z": {
                "x_mm": 60.0,
                "y_mm": 88.75,
                "z_mm": 0.015,
                "fuente": "MEASURED",
                "fecha": "2026-07-31T00:00:00+00:00",
                "maquina": "klipper",
                "homed_axes": "xyz",
                "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
            },
        })
        calls = {"capture": 0}

        def capture_probe_reference_observation() -> dict[str, object]:
            calls["capture"] += 1
            return {
                "position": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
                "machine_label": "http://moonraker.local",
                "homed_axes": "xyz",
                "session_id": "2026-08-01T00:00:00+00:00#serial-3",
            }

        runtime = self.app.state.machine_runtime
        runtime.capture_probe_reference_observation = capture_probe_reference_observation
        payload = {
            "grid_mode": "manual",
            "rows": 3,
            "columns": 4,
            "edge_margin_left_mm": 1.5,
            "edge_margin_right_mm": 2.5,
            "edge_margin_bottom_mm": 3.5,
            "edge_margin_top_mm": 4.5,
            "exclusions": [
                {
                    "id": "keepout-1",
                    "name": "Pinza",
                    "shape": "rectangle",
                    "enabled": True,
                    "x_min_mm": 8.0,
                    "x_max_mm": 12.0,
                    "y_min_mm": 6.0,
                    "y_max_mm": 9.0,
                }
            ],
            "max_spacing_mm": 7.5,
            "margin_mm": 0,
            "safe_z_mm": 11.0,
            "probe_step_mm": 0.04,
            "probe_feed_mm_min": 45.0,
            "retract_mm": 0.9,
        }

        response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/physical-map/plan-from-reference", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["capture"], 0)
        measured = response.json()["payload"]
        self.assertEqual(measured["status"], "MESH_PLANNED")
        self.assertFalse(measured["execution"]["worker_active"])
        self.assertEqual(measured["rows"], 3)
        self.assertEqual(measured["columns"], 4)
        self.assertEqual(measured["point_count"], 12)
        self.assertGreaterEqual(measured["arm_backend_duration_ms"], 0)
        self.assertEqual(measured["arm_point_count"], 12)
        self.assertEqual(measured["mesh_config"]["edge_margin_left_mm"], 1.5)
        self.assertEqual(measured["mesh_config"]["edge_margin_right_mm"], 2.5)
        self.assertEqual(measured["mesh_config"]["edge_margin_bottom_mm"], 3.5)
        self.assertEqual(measured["mesh_config"]["edge_margin_top_mm"], 4.5)
        self.assertEqual(measured["mesh_config"]["max_spacing_mm"], 7.5)
        self.assertEqual(measured["probe_config"]["safe_z_mm"], 11.0)
        self.assertEqual(measured["probe_config"]["probe_step_mm"], 0.04)
        self.assertEqual(measured["probe_config"]["probe_feed_mm_min"], 45.0)
        self.assertEqual(measured["probe_config"]["retract_mm"], 0.9)
        self.assertEqual(measured["exclusions"][0]["id"], "keepout-1")

        service = self.app.state.physical_map_service
        preview = service.preview_mesh_from_saved_reference(
            project_id=project_id,
            operation_id=operation_id,
            config=service.__class__.__dict__["preview_mesh"].__globals__["PhysicalMeshConfig"](
                grid_mode="manual",
                rows=3,
                columns=4,
                edge_margin_left_mm=1.5,
                edge_margin_right_mm=2.5,
                edge_margin_bottom_mm=3.5,
                edge_margin_top_mm=4.5,
                exclusions=(service.__class__.__dict__["preview_mesh"].__globals__["PhysicalExclusion"](
                    id="keepout-1",
                    name="Pinza",
                    shape="rectangle",
                    enabled=True,
                    x_min_mm=8.0,
                    x_max_mm=12.0,
                    y_min_mm=6.0,
                    y_max_mm=9.0,
                ),),
                max_spacing_mm=7.5,
                margin_mm=0,
                safe_z_mm=11.0,
                probe_step_mm=0.04,
                probe_feed_mm_min=45.0,
                retract_mm=0.9,
            ),
        )
        self.assertEqual(measured["point_count"], preview["point_count"])
        self.assertEqual(measured["grid"]["dx_mm"], preview["grid"]["dx_mm"])
        self.assertEqual(measured["grid"]["dy_mm"], preview["grid"]["dy_mm"])
        self.assertEqual(measured["local_region"], preview["local_region"])
        self.assertEqual(measured["points"][1]["x_local"], preview["points"][0]["x_local"])
        self.assertEqual(measured["points"][-1]["y_local"], preview["points"][-1]["y_local"])

    def test_preview_endpoint_is_pure_and_does_not_persist_physical_map(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._upload_and_analyze_operation(project_id, operation_id)
        self._write_setup_preparation(project_id, {
            "origen_trabajo": {
                "x_mm": 60.0,
                "y_mm": 88.75,
                "z_mm": None,
                "fuente": "MEASURED",
                "fecha": "2026-07-31T00:00:00+00:00",
                "maquina": "klipper",
                "homed_axes": "xyz",
                "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": None},
            },
            "referencia_z": {
                "x_mm": 60.0,
                "y_mm": 88.75,
                "z_mm": 0.015,
                "fuente": "MEASURED",
                "fecha": "2026-07-31T00:00:00+00:00",
                "maquina": "klipper",
                "homed_axes": "xyz",
                "sesion": "physical-session",
                "posicion_captura": {"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
            },
        })
        payload = {
            "grid_mode": "manual",
            "rows": 2,
            "columns": 2,
            "edge_margin_left_mm": 2.0,
            "edge_margin_right_mm": 2.0,
            "edge_margin_bottom_mm": 2.0,
            "edge_margin_top_mm": 2.0,
            "safe_z_mm": 10.0,
        }
        repository = self.app.state.physical_map_service.repository
        counts = {"project": 0, "map": 0}
        original_save_project = repository.save_project
        original_save_map = repository.save_height_map_payload

        def counting_save_project(project):
            counts["project"] += 1
            return original_save_project(project)

        def counting_save_map(project_id_arg, map_id_arg, map_payload):
            counts["map"] += 1
            return original_save_map(project_id_arg, map_id_arg, map_payload)

        with patch.object(repository, "save_project", side_effect=counting_save_project), patch.object(repository, "save_height_map_payload", side_effect=counting_save_map):
            response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/physical-map/preview", json=payload)

        self.assertEqual(response.status_code, 200)
        preview = response.json()["payload"]
        self.assertEqual(preview["status"], "MESH_PREVIEW")
        self.assertEqual(preview["point_count"], 4)
        self.assertEqual(counts["project"], 0)
        self.assertEqual(counts["map"], 0)
        service = self.app.state.physical_map_service
        history = service.history(project_id=project_id, operation_id=operation_id)
        self.assertEqual(history, [])
        project_payload = self.client.get(f"/api/projects/{project_id}").json()
        self.assertIsNone(project_payload["montajes"][0]["active_map_id"])

    def test_compensation_audit_uses_real_estimation_threshold_instead_of_line_count(self) -> None:
        self.app.state.compensated_gcode_service.build_comparison_report = lambda *_args, **_kwargs: {
            "selected_mode": "legacy",
            "recommended_mode": "adaptive_fast",
            "max_z_error_mm": 0.01,
            "original": {"mode": "original", "movements_total": 10, "unsupported_commands": [], "eligible": True},
            "legacy": {"mode": "legacy", "movements_total": 12, "unsupported_commands": [], "eligible": True},
            "adaptive_fast": {"mode": "adaptive_fast", "movements_total": 4, "unsupported_commands": [], "eligible": True},
            "warnings": [],
            "_artifacts": {"original": "G1 X1", "legacy": "G1 X2", "adaptive_fast": "G1 X3"},
        }

        def estimate_text(text: str) -> dict[str, object]:
            if text == "G1 X1":
                return {"estimated_time_s": 10.0, "method": "internal", "confidence": "medium", "unsupported_commands": []}
            if text == "G1 X2":
                return {"estimated_time_s": 10.0, "method": "internal", "confidence": "medium", "unsupported_commands": []}
            return {
                "estimated_time_s": 10.06,
                "method": "moonraker_analysis",
                "confidence": "high",
                "distribution_detail": "Moonraker aporta el tiempo total; la distribución temporal por file_position proviene del estimador interno escalado.",
                "unsupported_commands": [],
            }

        self.app.state.time_estimation_service.estimate_text = estimate_text

        response = self.client.post("/api/projects/proj/operations/op/compensation-audit")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recommended_mode"], "legacy")
        self.assertFalse(payload["adaptive_fast"]["eligible"])
        self.assertEqual(payload["adaptive_fast"]["estimation_method"], "moonraker_analysis")
        self.assertIn("interno escalado", payload["adaptive_fast"]["estimation_detail"])

    def test_get_physical_map_returns_latest_paused_state_without_writing_files(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._upload_and_analyze_operation(project_id, operation_id)
        service = self.app.state.physical_map_service
        planned = service.capture_reference_and_plan(
            project_id=project_id,
            operation_id=operation_id,
            machine_origin_x=50.0,
            machine_origin_y=40.0,
            reference_z=1.25,
            machine_position={"x_mm": 50.0, "y_mm": 40.0, "z_mm": 1.25},
            homed_axes="xyz",
            machine_label="test",
            session_id="session-1",
        )
        paused = service.mark_status(
            project_id=project_id,
            map_id=planned["map_id"],
            status="MESH_PAUSED",
            worker_active=False,
            point_state="MESH_PAUSED",
            last_event="Pausa persistida para recuperación.",
            metadata={
                "pause_requested": True,
                "pause_reason": "Solicitada por el operador.",
                "phase": "paused",
                "last_error": "Watchdog sin progreso.",
                "last_progress_at": "2026-08-01T00:00:00+00:00",
            },
        )
        map_file = self.data_dir / "projects" / project_id / "maps" / Path(planned["map_id"]) / "height_map.json"
        before_text = map_file.read_text(encoding="utf-8")
        before_mtime = map_file.stat().st_mtime_ns
        before_updated_at = paused["updated_at"]

        response = self.client.get(f"/api/projects/{project_id}/operations/{operation_id}/physical-map")

        self.assertEqual(response.status_code, 200)
        latest = response.json()["payload"]
        self.assertEqual(latest["map_id"], planned["map_id"])
        self.assertEqual(latest["status"], "MESH_PAUSED")
        self.assertEqual(latest["last_error"], "Watchdog sin progreso.")
        self.assertTrue(isinstance(latest["last_progress_age_s"], float))
        self.assertEqual(latest["updated_at"], before_updated_at)
        self.assertEqual(map_file.read_text(encoding="utf-8"), before_text)
        self.assertEqual(map_file.stat().st_mtime_ns, before_mtime)

    def test_pause_physical_map_is_idempotent_and_preserves_next_point(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self._upload_and_analyze_operation(project_id, operation_id)
        service = self.app.state.physical_map_service
        plan = service.capture_reference_and_plan(
            project_id=project_id,
            operation_id=operation_id,
            machine_origin_x=0.0,
            machine_origin_y=0.0,
            reference_z=10.0,
            machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
            homed_axes="xyz",
            machine_label="test",
            session_id="session",
        )

        class BlockingRuntime:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()
                self.calls: list[int] = []

            def snapshot(self) -> dict[str, object]:
                return {
                    "state": "MESH_PROBING",
                    "position": {"x": 0.0, "y": 0.0, "z": 10.0, "velocity": 0.0},
                    "homed_axes": "xyz",
                    "last_command_text": "probe_mesh_point",
                    "telemetry_age_s": 0.01,
                    "serial_age_s": 0.01,
                }

            def probe_mesh_point(self, point: dict[str, object], probe_config=None, progress_callback=None) -> dict[str, float]:
                self.calls.append(int(point["index"]))
                self.entered.set()
                if progress_callback is not None:
                    progress_callback("POINT_MOVE_XY", {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine")})
                self.release.wait(1.0)
                return {"z_measured": 9.95, "duration_s": 0.01}

        runtime = BlockingRuntime()
        self.app.state.mesh_execution_service.start_all(project_id=project_id, map_id=plan["map_id"], runtime=runtime)
        self.assertTrue(runtime.entered.wait(1.0))

        first = self.client.post(f"/api/projects/{project_id}/physical-maps/{plan['map_id']}/pause")
        second = self.client.post(f"/api/projects/{project_id}/physical-maps/{plan['map_id']}/pause")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.json()["payload"]
        second_payload = second.json()["payload"]
        self.assertEqual(first_payload["execution"]["point_state"], "MESH_PAUSING")
        self.assertTrue(first_payload["execution"]["pause_requested"])
        self.assertEqual(second_payload["execution"]["point_state"], "MESH_PAUSING")
        runtime.release.set()
        self.assertTrue(self.app.state.mesh_execution_service.wait_until_idle(timeout_s=3.0))
        final = service.get_by_id(project_id, plan["map_id"])
        self.assertEqual(final["status"], "MESH_PAUSED")
        self.assertTrue(final["execution"]["pause_requested"])
        self.assertEqual(final["next_point_index"], 2)
        self.assertEqual(runtime.calls, [1])


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
        operation_id = self._create_operation(project_id)
        self._upload_and_analyze_operation(project_id, operation_id)
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]
        physical_map = self.app.state.physical_map_service.capture_reference_and_plan(
            project_id=project_id,
            operation_id=operation_id,
            machine_origin_x=60.0,
            machine_origin_y=88.75,
            reference_z=0.015,
            machine_position={"x_mm": 60.0, "y_mm": 88.75, "z_mm": 0.015},
            homed_axes="xyz",
            machine_label="test",
            session_id="test-session",
            config=PhysicalMeshConfig(
                rows=3,
                columns=4,
                edge_margin_left_mm=1.5,
                edge_margin_right_mm=2.5,
                edge_margin_bottom_mm=3.5,
                edge_margin_top_mm=4.5,
                safe_z_mm=11.0,
                probe_step_mm=0.04,
                probe_feed_mm_min=45.0,
                retract_mm=0.9,
            ),
        )
        recipe_before = {
            "mesh_config": physical_map["mesh_config"],
            "probe_config": physical_map["probe_config"],
        }

        response = self.client.post(
            f"/api/projects/{project_id}/setups/{setup_id}/reset-preparation",
            json={},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["execution_reset"]["reason"], "preparation_reset")
        self.assertEqual(payload["execution_reset"]["current_runs_cleared"], 0)
        self.assertIn("machine_session", payload)
        self.assertIn("estado", payload["machine_session"])
        self.assertIn("runtime", payload)
        project_after = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(len(project_after["operaciones"]), 1)
        self.assertEqual(project_after["operaciones"][0]["archivo_gcode"], project["operaciones"][0]["archivo_gcode"])
        self.assertIsNone(project_after["montajes"][0]["active_reference_id"])
        self.assertIsNone(project_after["montajes"][0]["active_map_id"])
        archived_map = self.app.state.physical_map_service.get_by_id(project_id, physical_map["map_id"])
        self.assertEqual(archived_map["mesh_config"], recipe_before["mesh_config"])
        self.assertEqual(archived_map["probe_config"], recipe_before["probe_config"])

    def test_reset_setup_preparation_rejects_before_any_setup_or_runtime_mutation(self) -> None:
        project_id = self._create_project()
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]
        reset_runtime_calls = {"count": 0}

        def reject_reset(**_kwargs: object) -> dict[str, object]:
            raise ApplicationError("Movimiento físico activo.")

        def record_runtime_reset() -> dict[str, object]:
            reset_runtime_calls["count"] += 1
            return {}

        self.app.state.job_service.reset_runs_for_preparation = reject_reset
        self.app.state.machine_runtime.reset_physical_session = record_runtime_reset

        response = self.client.post(f"/api/projects/{project_id}/setups/{setup_id}/reset-preparation", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(reset_runtime_calls["count"], 0)
        project_after = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(project_after["montajes"][0], project["montajes"][0])

    def test_system_info_exposes_build_compatibility(self) -> None:
        payload = self.client.get("/api/system/info").json()
        self.assertIn("backend_version", payload)
        self.assertIn("frontend_build", payload)
        self.assertIn("git_commit", payload)
        self.assertEqual(payload["schema_version"], "1.7")
