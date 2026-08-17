from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import CompensatedGCodeService, PhysicalMapService, ProjectService, ReferenceSessionService
from klipper_cnc_assistant.execution import JobService, MoonrakerJobAdapter
from klipper_cnc_assistant.application.services import MachineSessionService
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
from klipper_cnc_assistant.storage import JsonProjectRepository


class FakeRuntime:
    def __init__(self) -> None:
        self.config = type("Config", (), {"moonraker_url": "http://moonraker.local", "moonraker_request_timeout_s": 2.0, "spindle_control_mode": "manual"})()
        self._last_probe = {"x_mm": 100.0, "y_mm": 100.0, "z_mm": 4.75}
        self.snapshot_payload = {
            "mode": "PHYSICAL",
            "moonraker": {"http_connected": True, "websocket_connected": True, "telemetry_state": "LIVE", "url": "http://moonraker.local"},
            "klipper": {"ready": True, "homed_axes": "xyz"},
            "started_at": "runtime-session",
        }
        self.refresh_observed_state_calls = 0

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self.snapshot_payload))

    def refresh_observed_state(self) -> dict:
        self.refresh_observed_state_calls += 1
        return self.snapshot()

    def move_to_tool_change_position(self) -> dict:
        return self.snapshot()

    def last_probe_position(self) -> dict[str, float]:
        return dict(self._last_probe)


class FakeAdapter:
    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime
        self.current_filename: str | None = None
        self.state = "standby"
        self.uploads: list[str] = []
        self.started: list[str] = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.cancel_calls = 0
        self.tool_change_moves = 0
        self.reference_moves: list[tuple[float, float]] = []
        self.spindle_stops = 0
        self.probe_calls = 0
        self._printing_seen = False
        self.status_sequence: list[dict] | None = None
        self.command_log: list[str] = []

    def runtime_snapshot(self) -> dict:
        return self.runtime.snapshot()

    def upload_file(self, *, local_path: Path, project_id: str, setup_id: str, face: str) -> dict:
        remote = f"klipper-cnc-assistant/{project_id}/{setup_id}/{face}/{local_path.name}"
        self.uploads.append(remote)
        self.current_filename = remote
        self.state = "complete"
        self._printing_seen = False
        return {"item": {"path": remote, "root": "gcodes"}, "print_started": True, "print_queued": False}

    def start_file(self, remote_path: str) -> dict:
        self.current_filename = remote_path
        self.started.append(remote_path)
        self.state = "complete"
        return {"started": remote_path}

    def pause(self) -> dict:
        self.pause_calls += 1
        self.state = "paused"
        return {"state": self.state}

    def resume(self) -> dict:
        self.resume_calls += 1
        self.state = "complete"
        return {"state": self.state}

    def cancel(self) -> dict:
        self.cancel_calls += 1
        self.state = "cancelled"
        return {"state": self.state}

    def print_status(self) -> dict:
        if self.status_sequence:
            payload = self.status_sequence[0]
            if len(self.status_sequence) > 1:
                self.status_sequence.pop(0)
            state = str(payload.get("state", self.state))
            filename = str(payload.get("filename", self.current_filename or "")) or self.current_filename
            progress = float(payload.get("progress", 1.0 if state == "complete" else 0.5))
            return {
                "connected": True,
                "klipper_state": "ready",
                "state": state,
                "filename": filename,
                "progress": progress,
                "is_active": bool(payload.get("is_active", state == "printing")),
                "file_position": payload.get("file_position", 49386),
                "file_size": payload.get("file_size", 119710),
                "print_duration": payload.get("print_duration", 0.0),
                "message": payload.get("message"),
                "updated_at": "2026-07-22T00:00:00+00:00",
            }
        state = self.state
        if self.state == "complete" and not self._printing_seen:
            self._printing_seen = True
            state = "printing"
        return {
            "connected": True,
            "klipper_state": "ready",
            "state": state,
            "filename": self.current_filename,
            "progress": 1.0 if self.state == "complete" else 0.5,
            "is_active": state == "printing",
            "file_position": 49386,
            "file_size": 119710,
            "print_duration": 0.0,
            "message": None,
            "updated_at": "2026-07-22T00:00:00+00:00",
        }

    def stop_spindle(self) -> dict:
        self.spindle_stops += 1
        self.command_log.append("M5")
        return {"stopped": True}

    def move_to_tool_change_position(self) -> dict:
        self.tool_change_moves += 1
        self.command_log.extend(["tool-change-z", "M400", "tool-change-xy", "M400"])
        return self.runtime.snapshot()

    def move_to_reference_point(self, *, x_mm: float, y_mm: float) -> dict:
        self.reference_moves.append((x_mm, y_mm))
        return {"accepted": True}

    def probe_tool_reference(self, *, x_mm: float, y_mm: float, probe_config: dict | None) -> dict:
        self.probe_calls += 1
        self.runtime._last_probe = {"x_mm": x_mm, "y_mm": y_mm, "z_mm": 4.5 - self.probe_calls * 0.1}
        return {"probe": dict(self.runtime._last_probe)}


class RecordingTimeEstimationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def estimate_text(self, text: str) -> dict[str, object]:
        return {
            "estimated_time_s": 40.0,
            "method": "internal",
            "confidence": "medium",
            "distribution_detail": "Distribución temporal calculada por el estimador interno.",
            "unsupported_commands": [],
        }

    def estimate_project_file(self, *, project_id: str, relative_path: str, remote_filename: str | None = None) -> dict[str, object]:
        self.calls.append({
            "project_id": project_id,
            "relative_path": relative_path,
            "remote_filename": remote_filename,
        })
        return {
            "estimated_time_s": 42.5 if remote_filename else 40.0,
            "method": "moonraker_analysis" if remote_filename else "internal",
            "confidence": "high" if remote_filename else "medium",
            "distribution_detail": "Moonraker aporta el tiempo total; la distribución temporal por file_position proviene del estimador interno escalado." if remote_filename else "Distribución temporal calculada por el estimador interno.",
            "offset_table": [
                {"file_byte_offset": 10.0, "predicted_cumulative_seconds": 20.0},
                {"file_byte_offset": 20.0, "predicted_cumulative_seconds": 42.5 if remote_filename else 40.0},
            ],
        }


class JobServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repository = JsonProjectRepository(Path(self.tempdir.name))
        self.project_service = ProjectService(self.repository)
        self.machine_session_service = MachineSessionService()
        self.reference_service = ReferenceSessionService(self.repository, None, self.machine_session_service, None)  # type: ignore[arg-type]
        self.physical_map_service = PhysicalMapService(self.repository)
        self.time_estimation_service = RecordingTimeEstimationService()
        self.compensated_service = CompensatedGCodeService(self.repository, self.physical_map_service, self.time_estimation_service)
        self.runtime = FakeRuntime()
        self.adapter = FakeAdapter(self.runtime)
        self.job_service = JobService(
            self.repository,
            self.physical_map_service,
            self.reference_service,
            self.compensated_service,
            self.runtime,
            time_estimation_service=self.time_estimation_service,
            adapter_factory=lambda runtime: self.adapter,
        )
        self.project = self.project_service.create_project(nombre="PCB test", ancho_mm=80.0, alto_mm=60.0)
        self.project_id = self.project.id
        self.setup_id = self.project.montajes[0].id
        self._create_operation("Fresado superior", "aislamiento", 0, "vbit-30", "V-bit 30°", "G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.050 F120\n")
        self._create_operation("Fresado acabado", "aislamiento", 1, "vbit-30", "V-bit 30°", "G21\nG90\nG0 X10 Y20\nG1 X20 Y20 Z-0.050 F120\n")
        self._create_operation("Taladrado 0.8", "taladrado", 2, "drill-08", "Broca 0.8 mm", "G21\nG90\nG0 X15 Y15\nG1 X15 Y15 Z-0.100 F120\n")
        self._create_operation("Corte", "corte exterior", 3, "mill-10", "Fresa 1.0 mm", "G21\nG90\nG0 X12 Y12\nG1 X18 Y18 Z-0.120 F120\n")
        self._create_measured_map()

    def _create_operation(self, nombre: str, tipo: str, orden: int, tool_id: str, herramienta: str, gcode: str) -> None:
        op = self.project_service.add_operation(
            project_id=self.project_id,
            nombre=nombre,
            tipo=tipo,
            cara="superior",
            orden=orden,
            setup_id=self.setup_id,
            tool_id=tool_id,
            herramienta=herramienta,
        )
        self.project_service.upload_operation_gcode(project_id=self.project_id, operation_id=op.id, filename=f"{op.id}.gcode", content=gcode)
        self.project_service.analyze_operation(project_id=self.project_id, operation_id=op.id)

    def _create_measured_map(self) -> None:
        first = self.project_service.get_project(self.project_id).operations_for_setup(self.setup_id)[0]
        payload = self.physical_map_service.capture_reference_and_plan(
            project_id=self.project_id,
            operation_id=first.id,
            machine_origin_x=100.0,
            machine_origin_y=100.0,
            reference_z=5.0,
            machine_position={"x_mm": 100.0, "y_mm": 100.0, "z_mm": 5.0},
            homed_axes="xyz",
            machine_label="moonraker-test",
            session_id="session-1",
            config=PhysicalMeshConfig(rows=2, columns=2, edge_margin_left_mm=2.0, edge_margin_right_mm=2.0, edge_margin_bottom_mm=2.0, edge_margin_top_mm=2.0),
        )
        for index, z in enumerate((5.0, 5.01, 5.02, 5.01, 4.99)):
            self.physical_map_service.record_point(project_id=self.project_id, map_id=payload["map_id"], point_index=index, z_measured=z, attempts=1, duration_s=0.1)

    def _create_same_diameter_distinct_tool_project(self, *, legacy_diameter_ids: bool = False) -> tuple[str, str, str]:
        project = self.project_service.create_project(nombre="PCB etiquetas", ancho_mm=80.0, alto_mm=60.0)
        project_id = project.id
        setup_id = project.montajes[0].id
        definitions = [
            ("Fresado superior", "aislamiento", 0, "tool-a-0.8", "0.8", "G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.050 F120\n"),
            ("Taladrado_1", "taladrado", 1, "tool-b-0.8-mm", "0.8 MM", "G21\nG90\nG0 X15 Y15\nG1 X15 Y15 Z-0.100 F120\n"),
            ("Taladrado_2", "taladrado", 2, "tool-c-1-mm", "1 MM", "G21\nG90\nG0 X18 Y18\nG1 X18 Y18 Z-0.100 F120\n"),
            ("Taladrado_3", "taladrado", 3, "tool-d-3-mm", "3 mm", "G21\nG90\nG0 X22 Y22\nG1 X22 Y22 Z-0.100 F120\n"),
            ("Contorno", "contorno", 4, "tool-e-1.2", "1.2", "G21\nG90\nG0 X12 Y12\nG1 X18 Y18 Z-0.120 F120\n"),
        ]
        operation_ids: list[str] = []
        tool_ids_by_operation: dict[str, str] = {}
        for nombre, tipo, orden, tool_id, herramienta, gcode in definitions:
            operation = self.project_service.add_operation(
                project_id=project_id,
                nombre=nombre,
                tipo=tipo,
                cara="superior",
                orden=orden,
                setup_id=setup_id,
                tool_id=tool_id,
                herramienta=herramienta,
            )
            operation_ids.append(operation.id)
            tool_ids_by_operation[operation.id] = tool_id
            self.project_service.upload_operation_gcode(project_id=project_id, operation_id=operation.id, filename=f"{operation.id}.gcode", content=gcode)
            self.project_service.analyze_operation(project_id=project_id, operation_id=operation.id)
        first_operation_id = operation_ids[0]
        payload = self.physical_map_service.capture_reference_and_plan(
            project_id=project_id,
            operation_id=first_operation_id,
            machine_origin_x=80.0,
            machine_origin_y=109.15,
            reference_z=104.9,
            machine_position={"x_mm": 80.0, "y_mm": 109.15, "z_mm": 104.9},
            homed_axes="xyz",
            machine_label="http://127.0.0.1:7126",
            session_id="session-current",
            config=PhysicalMeshConfig(rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=13.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=6.0),
        )
        for index, z in enumerate((104.9, 104.91, 104.89, 104.92)):
            self.physical_map_service.record_point(project_id=project_id, map_id=payload["map_id"], point_index=index, z_measured=z, attempts=1, duration_s=0.1)
        if legacy_diameter_ids:
            project_file = self.repository.project_dir(project_id) / "project.json"
            payload_project = json.loads(project_file.read_text(encoding="utf-8"))
            for item in payload_project["operaciones"]:
                if item["id"] in {operation_ids[0], operation_ids[1]}:
                    item["tool_id"] = "tool-diam-0.8-mm"
            project_file.write_text(json.dumps(payload_project, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
            map_file = self.repository.project_dir(project_id) / "maps" / Path(payload["map_id"]) / "height_map.json"
            map_payload = json.loads(map_file.read_text(encoding="utf-8"))
            reference = dict((map_payload.get("tool_references") or {}).get(tool_ids_by_operation[first_operation_id]) or {})
            map_payload["acquisition_tool_id"] = "tool-diam-0.8-mm"
            map_payload["tool_references"] = {
                "tool-diam-0.8-mm": {
                    **reference,
                    "tool_id": "tool-diam-0.8-mm",
                }
            }
            map_file.write_text(json.dumps(map_payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return project_id, setup_id, payload["map_id"]

    def test_manual_spindle_mode_never_sends_spindle_gcode(self) -> None:
        sent_commands: list[str] = []

        class RecordingClient:
            def send_gcode(self, command: str) -> dict[str, object]:
                sent_commands.append(command)
                return {"command": command}

        adapter = MoonrakerJobAdapter(self.runtime, client_factory=lambda *_args, **_kwargs: RecordingClient())

        result = adapter.stop_spindle()

        self.assertEqual(result["mode"], "manual")
        self.assertFalse(result["command_sent"])
        self.assertEqual(sent_commands, [])
        self.assertTrue({"M3", "M4", "M5"}.isdisjoint(sent_commands))

    def test_job_run_requires_manual_spindle_stop_before_tool_change_transition(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(run["available_actions"], ["confirm-spindle-stopped", "cancel"])
        self.assertEqual(run["operations"][0]["execution_status"], "COMPLETED")
        self.assertEqual(run["operations"][1]["execution_status"], "COMPLETED")
        self.assertEqual(self.adapter.tool_change_moves, 0)
        self.assertEqual(self.adapter.command_log, [])
        self.assertIn("Apague manualmente el spindle", run["next_action"])

    def test_confirm_spindle_stopped_rejects_active_print(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        self.adapter.status_sequence = [{"state": "printing", "is_active": True}]

        with self.assertRaisesRegex(Exception, "sigue imprimiendo"):
            self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")

        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(self.adapter.tool_change_moves, 0)
        self.assertEqual(self.runtime.refresh_observed_state_calls, 0)

    def test_confirm_spindle_stopped_rejects_missing_homing(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        self.runtime.snapshot_payload["klipper"]["homed_axes"] = "xy"

        with self.assertRaisesRegex(Exception, "Falta homing XYZ"):
            self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")

        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(self.adapter.tool_change_moves, 0)
        self.assertEqual(self.runtime.refresh_observed_state_calls, 1)

    def test_confirm_spindle_stopped_moves_z_before_xy_and_enters_tool_change_required(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(run["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(run["available_actions"], ["confirm-tool-change", "cancel"])
        self.assertEqual(self.adapter.tool_change_moves, 1)
        self.assertLess(self.adapter.command_log.index("tool-change-z"), self.adapter.command_log.index("tool-change-xy"))
        self.assertEqual(self.adapter.command_log[:4], ["tool-change-z", "M400", "tool-change-xy", "M400"])
        self.assertTrue({"M3", "M4", "M5"}.isdisjoint(self.adapter.command_log))
        self.assertEqual(self.runtime.refresh_observed_state_calls, 1)

    def test_prepare_run_binds_initial_reference_only_to_installed_tool(self) -> None:
        project_id, setup_id, map_id = self._create_same_diameter_distinct_tool_project()
        plan = self.job_service.generate_project_compensation(project_id=project_id, setup_id=setup_id, face="superior")
        map_file = self.repository.project_dir(project_id) / "maps" / Path(map_id) / "height_map.json"
        map_before = json.loads(map_file.read_text(encoding="utf-8"))

        refreshed_plan = self.job_service.get_plan(project_id=project_id, setup_id=setup_id, face="superior")
        run = self.job_service.prepare_run(project_id=project_id, setup_id=setup_id, face="superior")
        project = self.project_service.get_project(project_id)
        operations = sorted(project.operations_for_setup(setup_id), key=lambda item: item.orden)
        tool_ids = [operation.tool_id for operation in operations]
        reference_statuses = [item["reference_status"] for item in refreshed_plan["operations"]]

        self.assertEqual(tool_ids, ["tool-a-0.8", "tool-b-0.8-mm", "tool-c-1-mm", "tool-d-3-mm", "tool-e-1.2"])
        self.assertEqual(refreshed_plan["summary"]["distinct_tools"], 5)
        self.assertEqual(refreshed_plan["summary"]["tool_changes"], 4)
        self.assertEqual(reference_statuses, ["LISTA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA"])
        self.assertEqual(run["state"], "JOB_READY")
        self.assertTrue(run["ready"])
        checks = {item["name"]: item["ok"] for item in run["checks"]}
        self.assertTrue(checks["referencia_inicial"])
        active_reference_id = project.get_setup(setup_id).active_reference_id
        self.assertEqual(run["operations"][0]["installation_revision"], active_reference_id)
        self.assertIsNone(run["operations"][1]["installation_revision"])
        self.assertEqual(run["operations"][1]["reference_status"], "REQUIERE_REFERENCIA")
        self.assertEqual(run["operations"][4]["reference_status"], "REQUIERE_REFERENCIA")
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(self.adapter.command_log, [])
        self.assertEqual(self.adapter.reference_moves, [])
        map_after = json.loads(map_file.read_text(encoding="utf-8"))
        self.assertEqual(map_after["points"], map_before["points"])
        self.assertEqual(map_after["acquisition_reference_z"], map_before["acquisition_reference_z"])
        self.assertEqual(map_after["tool_references"], map_before["tool_references"])
        manifest = self.repository.project_dir(project_id) / refreshed_plan["manifest_path"]
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual([item["reference_status"] for item in manifest_payload["operations"]], ["LISTA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA"])

    def test_legacy_diameter_tool_ids_are_migrated_to_distinct_tool_labels(self) -> None:
        project_id, setup_id, _map_id = self._create_same_diameter_distinct_tool_project(legacy_diameter_ids=True)

        refreshed_plan = self.job_service.get_plan(project_id=project_id, setup_id=setup_id, face="superior")
        project = self.project_service.get_project(project_id)
        operations = sorted(project.operations_for_setup(setup_id), key=lambda item: item.orden)

        self.assertEqual([operation.tool_id for operation in operations], ["tool-label-0.8", "tool-label-0.8-mm", "tool-c-1-mm", "tool-d-3-mm", "tool-e-1.2"])
        self.assertEqual(refreshed_plan["summary"]["distinct_tools"], 5)
        self.assertEqual(refreshed_plan["summary"]["tool_changes"], 4)
        self.assertEqual([item["reference_status"] for item in refreshed_plan["operations"]], ["LISTA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA", "REQUIERE_REFERENCIA"])

    def test_archive_stale_run_rejects_fresh_job_validating_even_if_idle(self) -> None:
        project_id, setup_id, _map_id = self._create_same_diameter_distinct_tool_project()
        self.job_service.generate_project_compensation(project_id=project_id, setup_id=setup_id, face="superior")
        run = self.job_service.prepare_run(project_id=project_id, setup_id=setup_id, face="superior")
        self.assertEqual(run["state"], "JOB_READY")
        run["state"] = "JOB_VALIDATING"
        run["ready"] = False
        context = self.job_service._context(project_id, setup_id, "superior")
        self.job_service._save_run(context, run)
        self.adapter.status_sequence = [{"state": "standby", "filename": "", "progress": 0.0, "is_active": False}]

        with self.assertRaisesRegex(Exception, "criterio real de obsolescencia"):
            self.job_service.archive_stale_run(project_id=project_id, setup_id=setup_id, face="superior")

    def test_job_plan_groups_tools_and_writes_manifest(self) -> None:
        plan = self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(plan["summary"]["operations_total"], 4)
        self.assertEqual(plan["summary"]["tool_changes"], 2)
        self.assertEqual(plan["summary"]["generated_files"], 4)
        self.assertTrue(plan["manifest_path"])
        self.assertEqual(plan["operations"][0]["tool_name"], "V-bit 30°")
        self.assertEqual(plan["operations"][1]["tool_name"], "V-bit 30°")
        self.assertTrue(plan["operations"][2]["tool_changed"])
        manifest = self.repository.project_dir(self.project_id) / plan["manifest_path"]
        self.assertTrue(manifest.exists())
        generated = self.repository.project_dir(self.project_id) / plan["operations"][0]["generated_file"]
        output = generated.read_text(encoding="utf-8")
        self.assertIn("X120.00000 Y110.00000", output)
        self.assertIn("Z4.", output)
        self.assertNotIn("X20.00000 Y10.00000 Z-0.05000", output)

    def test_dry_run_uses_compensated_plan_without_adapter_calls(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        result = self.job_service.dry_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "DRY_RUN")
        self.assertFalse(result["movement_lock_acquired"])
        self.assertEqual(result["moonraker_commands_sent"], 0)
        self.assertEqual(self.adapter.started, [])
        self.assertEqual(len(result["operations"]), 4)
        self.assertIsNotNone(result["operations"][0]["plan_hash"])

    def test_job_run_executes_all_operations_with_two_manual_spindle_confirmations(self) -> None:
        initial_plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertTrue(all(item["generated_file"] is None for item in initial_plan["operations"]))

        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertIsNotNone(run["operations"][0]["generated_file"])
        self.assertIsNotNone(run["operations"][1]["generated_file"])
        self.assertIsNone(run["operations"][2]["generated_file"])
        self.assertIsNone(run["operations"][3]["generated_file"])

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "TOOL_CHANGE_REQUIRED")

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "READY_TO_RESUME")
        self.assertEqual(len(self.adapter.started), 0)
        self.assertIsNone(run["operations"][2]["generated_file"])
        self.assertIsNone(run["operations"][3]["generated_file"])

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="continue")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(run["operations"][2]["execution_status"], "COMPLETED")
        self.assertIsNotNone(run["operations"][2]["generated_file"])
        self.assertIsNone(run["operations"][3]["generated_file"])

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "TOOL_CHANGE_REQUIRED")

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "READY_TO_RESUME")
        self.assertIsNone(run["operations"][3]["generated_file"])
        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="continue")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(run["state"], "JOB_COMPLETE")
        self.assertEqual(run["summary"]["operations_completed"], 4)
        self.assertEqual(self.adapter.probe_calls, 2)
        self.assertEqual(self.adapter.tool_change_moves, 2)
        self.assertEqual(self.adapter.spindle_stops, 0)
        self.assertEqual(self.adapter.reference_moves, [(100.0, 100.0), (100.0, 100.0)])
        self.assertEqual(len(self.adapter.started), 0)
        self.assertEqual(run["operations"][3]["execution_status"], "COMPLETED")
        self.assertTrue({"M3", "M4", "M5"}.isdisjoint(self.adapter.command_log))

    def test_retry_tool_change_transition_does_not_repeat_completed_operation(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        original_move = self.adapter.move_to_tool_change_position
        calls = {"count": 0}

        def fail_once() -> dict:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("tool change blocked")
            return original_move()

        self.adapter.move_to_tool_change_position = fail_once  # type: ignore[assignment]
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "RECOVERY_REQUIRED")
        self.assertEqual(run["operations"][1]["execution_status"], "COMPLETED")
        uploads_before = list(self.adapter.uploads)

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="retry-tool-change-transition")
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(run["operations"][1]["execution_status"], "COMPLETED")
        self.assertEqual(self.adapter.uploads, uploads_before)

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(run["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(run["operations"][1]["execution_status"], "COMPLETED")
        self.assertEqual(self.adapter.uploads, uploads_before)
        self.assertEqual(calls["count"], 2)

    def test_live_execution_reports_running_progress_from_current_run(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.adapter.status_sequence = [
            {"state": "printing", "progress": 0.553, "is_active": True},
        ]
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        time.sleep(0.7)
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))
        operation = persisted["operations"][0]
        self.assertEqual(operation["execution_status"], "RUNNING")
        self.assertTrue(operation["observed_printing"])
        self.assertEqual(operation["remote_file"], self.adapter.uploads[0])
        self.assertAlmostEqual(operation["progress"], 0.553, places=3)
        live = self.job_service.live_execution(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(live["moonraker"]["print_state"], "printing")
        self.assertEqual(live["operation"]["execution_status"], "RUNNING")
        self.assertAlmostEqual(live["operation"]["progress"], 0.553, places=3)
        estimates = [float(item["estimated_time_s"]) for item in persisted["operations"]]
        expected_weighted_progress = estimates[0] * 0.553 / sum(estimates)
        self.assertAlmostEqual(live["run"]["overall_progress"], expected_weighted_progress, places=3)
        self.adapter.status_sequence = [{"state": "cancelled", "progress": 0.553, "is_active": False}]
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]

    def test_live_execution_falls_back_to_operation_count_without_double_counting_completed_current(self) -> None:
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run["state"] = "NEXT_OPERATION_READY"
        run["current_operation_index"] = 0
        run["operations"][0]["execution_status"] = "COMPLETED"
        run["operations"][0]["progress"] = 1.0
        run["summary"]["operations_completed"] = 1
        self.job_service._save_run(context, run)

        live = self.job_service.live_execution(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertAlmostEqual(live["run"]["overall_progress"], 1.0 / 4.0, places=3)

    def test_live_execution_rejects_complete_without_printing(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.adapter.status_sequence = [
            {"state": "complete", "progress": 1.0, "is_active": False},
        ]
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        time.sleep(0.7)
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))
        self.assertNotEqual(persisted["operations"][0]["execution_status"], "COMPLETED")
        self.assertFalse(persisted["operations"][0].get("observed_printing", False))
        self.adapter.status_sequence = [{"state": "cancelled", "progress": 1.0, "is_active": False}]
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]

    def test_live_execution_detects_filename_mismatch(self) -> None:
        self.job_service.generate_project_compensation(
            project_id=self.project_id,
            setup_id=self.setup_id,
            face="superior",
        )
        run = self.job_service.prepare_run(
            project_id=self.project_id,
            setup_id=self.setup_id,
            face="superior",
        )
        context = self.job_service._context(
            self.project_id,
            self.setup_id,
            "superior",
        )
        operation = run["operations"][0]
        expected = self.job_service._expected_remote_file(
            context,
            str(operation["generated_file"]),
        )
        operation["remote_file"] = expected
        operation["execution_status"] = "RUNNING"
        operation["observed_printing"] = True
        operation["progress"] = 0.553
        run["state"] = "OPERATION_RUNNING"
        self.job_service._save_run(context, run)

        self.adapter.status_sequence = [
            {
                "state": "printing",
                "filename": "otro/archivo.gcode",
                "progress": 0.553,
                "is_active": True,
            },
        ]

        live = self.job_service.live_execution(
            project_id=self.project_id,
            setup_id=self.setup_id,
            face="superior",
        )

        self.assertFalse(live["synchronization"]["ok"])
        self.assertEqual(
            live["synchronization"]["reason"],
            "filename_mismatch",
        )

    def test_eta_snapshot_keeps_moonraker_method_with_scaled_internal_distribution(self) -> None:
        run = {"eta_ratio_ema": 1.0}
        operation = {
            "time_estimate": {
                "estimated_time_s": 42.5,
                "method": "moonraker_analysis",
                "confidence": "high",
                "distribution_detail": "Moonraker aporta el tiempo total; la distribución temporal por file_position proviene del estimador interno escalado.",
                "offset_table": [
                    {"file_byte_offset": 10.0, "predicted_cumulative_seconds": 20.0},
                    {"file_byte_offset": 20.0, "predicted_cumulative_seconds": 42.5},
                ],
            }
        }
        status = {
            "file_position": 10.0,
            "progress": 0.5,
            "print_duration": 19.0,
            "print_state": "printing",
        }

        eta = self.job_service._build_eta_snapshot(
            run=run,
            operation=operation,
            status=status,
            expected_filename="job.gcode",
            observed_filename="job.gcode",
        )

        self.assertTrue(eta["available"])
        self.assertEqual(eta["method"], "moonraker_analysis")
        self.assertIn("interno escalado", eta["detail"])

    def test_post_upload_analysis_uses_remote_item_path_and_updates_metadata(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")

        self.job_service._execute_next_operation(context, run)

        persisted = self.job_service._load_run(context)
        assert persisted is not None
        operation = persisted["operations"][0]
        self.assertEqual(self.time_estimation_service.calls[-1]["remote_filename"], operation["remote_file"])
        self.assertEqual(operation["time_estimate"]["method"], "moonraker_analysis")
        metadata_path = self.repository.project_dir(self.project_id) / str(operation["generated_metadata"])
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["time_estimate"]["method"], "moonraker_analysis")
        self.assertEqual(metadata["time_estimate"]["estimated_time_s"], 42.5)

    def test_production_plan_uses_legacy_when_operation_keeps_adaptive_compatibility_setting(self) -> None:
        operation = self.project_service.get_project(self.project_id).operations_for_setup(self.setup_id)[0]
        self.project_service.update_operation(
            project_id=self.project_id,
            operation_id=operation.id,
            nombre=operation.nombre,
            compensation_mode="adaptive_fast",
            max_z_error_mm=operation.max_z_error_mm,
        )

        original_build_report = self.compensated_service.build_comparison_report

        def force_experimental(project_id: str, operation_id: str) -> dict[str, object]:
            report = original_build_report(project_id, operation_id)
            if operation_id == operation.id:
                report["adaptive_fast"]["eligible"] = False
                report["adaptive_fast"]["executable"] = False
                report["adaptive_fast"]["error"] = "Adaptive_fast solo puede descargarse como experimental."
                report["recommended_mode"] = "legacy"
            return report

        self.compensated_service.build_comparison_report = force_experimental  # type: ignore[assignment]
        try:
            plan = self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        finally:
            self.compensated_service.build_comparison_report = original_build_report  # type: ignore[assignment]

        production_row = next(item for item in plan["operations"] if item["operation_id"] == operation.id)
        self.assertIsNotNone(production_row["generated_file"])
        self.assertEqual(production_row["compensation_mode"], "legacy")
        self.assertIn("_legacy_compensated.gcode", str(production_row["generated_file"]))

    def test_missing_safe_rapid_height_keeps_adaptive_experimental_and_legacy_available(self) -> None:
        operation = self.project_service.get_project(self.project_id).operations_for_setup(self.setup_id)[0]
        self.project_service.update_operation(
            project_id=self.project_id,
            operation_id=operation.id,
            nombre=operation.nombre,
            compensation_mode="adaptive_fast",
            max_z_error_mm=operation.max_z_error_mm,
        )

        report = self.compensated_service.build_comparison_report(self.project_id, operation.id)

        self.assertFalse(report["adaptive_fast"]["eligible"])
        self.assertFalse(report["adaptive_fast"]["executable"])
        self.assertTrue(report["adaptive_fast"]["experimental_available"])
        self.assertIsNone(report["adaptive_fast"]["configured_safe_z_mm"])
        self.assertIn("falta una altura segura de desplazamiento configurada", str(report["adaptive_fast"]["error"]))
        self.assertTrue(report["legacy"]["executable"])

    def test_paused_eta_does_not_consume_print_duration(self) -> None:
        run = {"eta_ratio_ema": 1.0}
        operation = {
            "time_estimate": {
                "estimated_time_s": 30.0,
                "method": "internal",
                "confidence": "medium",
                "offset_table": [
                    {"file_byte_offset": 10.0, "predicted_cumulative_seconds": 10.0},
                    {"file_byte_offset": 20.0, "predicted_cumulative_seconds": 20.0},
                    {"file_byte_offset": 30.0, "predicted_cumulative_seconds": 30.0},
                ],
            }
        }
        status = {
            "file_position": 10.0,
            "progress": 0.333,
            "print_duration": 9.0,
            "print_state": "paused",
        }

        first = self.job_service._build_eta_snapshot(
            run=run,
            operation=operation,
            status=status,
            expected_filename="job.gcode",
            observed_filename="job.gcode",
        )
        second = self.job_service._build_eta_snapshot(
            run=run,
            operation=operation,
            status=status,
            expected_filename="job.gcode",
            observed_filename="job.gcode",
        )

        self.assertTrue(first["available"])
        self.assertEqual(first["elapsed_s"], second["elapsed_s"])
        self.assertEqual(first["remaining_s"], second["remaining_s"])

    def test_watcher_error_is_persisted(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        def boom() -> dict:
            raise RuntimeError("watcher exploded")
        self.adapter.print_status = boom  # type: ignore[assignment]
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))
        self.assertEqual(persisted["state"], "JOB_ERROR")
        self.assertIn("watcher exploded", persisted["last_watcher_error"])

    def test_recover_active_print_reuses_existing_moonraker_job(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service._load_run(context)
        assert run is not None
        run["state"] = "JOB_ERROR"
        self.job_service._save_run(context, run)
        expected = self.job_service._expected_remote_file(context, str(run["operations"][0]["generated_file"]))
        self.adapter.status_sequence = [
            {"state": "printing", "filename": expected, "progress": 0.553, "is_active": True},
            {"state": "printing", "filename": expected, "progress": 0.553, "is_active": True},
        ]

        recovered = self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        time.sleep(0.7)
        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))

        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(recovered["recovery_state"], "RECOVERED_ACTIVE_PRINT")
        self.assertEqual(persisted["recovery_state"], "RECOVERED_ACTIVE_PRINT")
        self.assertEqual(persisted["operations"][0]["remote_file"], expected)
        self.assertEqual(persisted["operations"][0]["execution_status"], "RUNNING")
        self.assertTrue(persisted["operations"][0]["observed_printing"])
        self.assertAlmostEqual(persisted["operations"][0]["progress"], 0.553, places=3)

        live = self.job_service.live_execution(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(live["run"]["recovery_state"], "RECOVERED_ACTIVE_PRINT")
        self.assertEqual(live["operation"]["expected_remote_file"], expected)
        self.assertAlmostEqual(live["operation"]["progress"], 0.553, places=3)

        self.adapter.status_sequence = [{"state": "cancelled", "filename": expected, "progress": 0.553, "is_active": False}]
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]

    def test_recovered_active_print_accepts_complete(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service._load_run(context)
        assert run is not None
        run["state"] = "JOB_ERROR"
        for item in run["operations"][1:]:
            item["execution_status"] = "COMPLETED"
        run["summary"]["operations_completed"] = 3
        self.job_service._save_run(context, run)
        expected = self.job_service._expected_remote_file(context, str(run["operations"][0]["generated_file"]))
        self.adapter.status_sequence = [
            {"state": "printing", "filename": expected, "progress": 0.553, "is_active": True},
            {"state": "complete", "filename": expected, "progress": 1.0, "is_active": False},
        ]

        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))

        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(persisted["operations"][0]["execution_status"], "COMPLETED")
        self.assertEqual(persisted["summary"]["operations_completed"], 4)
        self.assertEqual(persisted["state"], "JOB_COMPLETE")

    def test_recover_active_print_rejects_filename_mismatch(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service._load_run(context)
        assert run is not None
        run["state"] = "JOB_ERROR"
        self.job_service._save_run(context, run)
        self.adapter.status_sequence = [
            {"state": "printing", "filename": "klipper-cnc-assistant/otro/archivo.gcode", "progress": 0.553, "is_active": True},
        ]

        with self.assertRaisesRegex(Exception, "JOB_ACTIVE_CONFLICT"):
            self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(persisted["state"], "RECOVERY_REQUIRED")
        self.assertEqual(persisted["recovery_state"], "RECOVERY_REQUIRED")

    def test_start_run_refreshes_job_validating_before_reporting_blockers(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run["state"] = "JOB_VALIDATING"
        self.job_service._save_run(context, run)
        self.runtime.snapshot = lambda: {  # type: ignore[method-assign]
            "mode": "PHYSICAL",
            "moonraker": {"http_connected": True, "websocket_connected": True, "telemetry_state": "LIVE", "url": "http://moonraker.local"},
            "klipper": {"ready": True, "homed_axes": "xy"},
            "started_at": "runtime-session",
        }

        with self.assertRaisesRegex(Exception, "El trabajo no está listo para iniciar"):
            self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        persisted = json.loads(self.job_service._run_file(context).read_text(encoding="utf-8"))
        self.assertEqual(persisted["state"], "JOB_VALIDATING")
        self.assertFalse(persisted["ready"])
        self.assertEqual(self.adapter.uploads, [])

    def test_describe_run_conflict_reports_structured_dead_jobrun(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run["state"] = "JOB_VALIDATING"
        self.job_service._save_run(context, run)
        self.adapter.status_sequence = [{"state": "standby", "filename": "", "progress": 0.0, "is_active": False}]

        detail = self.job_service.describe_run_conflict(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(detail["code"], "JOB_ACTIVE_CONFLICT")
        self.assertEqual(detail["existing_run"]["run_id"], run["run_id"])
        self.assertEqual(detail["existing_run"]["status"], "JOB_VALIDATING")
        self.assertFalse(detail["existing_run"]["worker_alive"])
        self.assertFalse(detail["moonraker"]["is_active"])
        self.assertFalse(detail["can_archive_stale"])
        self.assertNotIn("archive-stale", detail["available_actions"])

    def test_archive_stale_run_rejects_active_moonraker_print(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run["state"] = "WAITING_FOR_KLIPPER"
        self.job_service._save_run(context, run)
        self.adapter.status_sequence = [{"state": "printing", "filename": "active.gcode", "progress": 0.42, "is_active": True}]

        with self.assertRaisesRegex(Exception, "No se puede archivar la ejecución obsoleta"):
            self.job_service.archive_stale_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertTrue(self.job_service._run_file(context).exists())

    def test_archive_stale_run_preserves_map_reference_and_compensated_files(self) -> None:
        plan = self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        context = self.job_service._context(self.project_id, self.setup_id, "superior")
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run["state"] = "WAITING_FOR_KLIPPER"
        run["available_actions"] = ["pause", "cancel"]
        run["updated_at"] = "2026-07-23T00:00:00+00:00"
        self.job_service._save_run(context, run)
        self.adapter.status_sequence = [{"state": "standby", "filename": "", "progress": 0.0, "is_active": False}]
        setup_before = self.project_service.get_project(self.project_id).get_setup(self.setup_id)
        generated_files = [self.repository.project_dir(self.project_id) / item["generated_file"] for item in plan["operations"] if item.get("generated_file")]

        result = self.job_service.archive_stale_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(result["archived_run_id"], run["run_id"])
        self.assertEqual(result["previous_status"], "WAITING_FOR_KLIPPER")
        self.assertTrue(result["can_start_new_run"])
        self.assertIn("job_run.current_run", result["locks_released"])
        archive_path = self.repository.project_dir(self.project_id) / result["archive_path"]
        self.assertTrue(archive_path.exists())
        archived = json.loads(archive_path.read_text(encoding="utf-8"))
        self.assertEqual(archived["state"], "STALE_RUN_ARCHIVED")
        self.assertEqual(archived["previous_status"], "WAITING_FOR_KLIPPER")
        self.assertFalse(self.job_service._run_file(context).exists())
        setup_after = self.project_service.get_project(self.project_id).get_setup(self.setup_id)
        self.assertEqual(setup_after.active_map_id, setup_before.active_map_id)
        self.assertEqual(setup_after.preparacion.referencia_z, setup_before.preparacion.referencia_z)
        for generated in generated_files:
            self.assertTrue(generated.exists())
        new_run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(new_run["state"], "JOB_READY")
        self.assertTrue(self.job_service._run_file(context).exists())


if __name__ == "__main__":
    unittest.main()
