from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import CompensatedGCodeService, JobService, PhysicalMapService, ProjectService, ReferenceSessionService
from klipper_cnc_assistant.application.services import MachineSessionService
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
from klipper_cnc_assistant.storage import JsonProjectRepository


class FakeRuntime:
    def __init__(self) -> None:
        self.config = type("Config", (), {"moonraker_url": "http://moonraker.local", "moonraker_request_timeout_s": 2.0})()
        self._last_probe = {"x_mm": 100.0, "y_mm": 100.0, "z_mm": 4.75}

    def snapshot(self) -> dict:
        return {
            "mode": "PHYSICAL",
            "moonraker": {"http_connected": True, "websocket_connected": True, "url": "http://moonraker.local"},
            "klipper": {"ready": True, "homed_axes": "xyz"},
            "started_at": "runtime-session",
        }

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
        self.probe_calls = 0

    def runtime_snapshot(self) -> dict:
        return self.runtime.snapshot()

    def upload_file(self, *, local_path: Path, project_id: str, setup_id: str, face: str) -> dict:
        remote = f"klipper-cnc-assistant/{project_id}/{setup_id}/{face}/{local_path.name}"
        self.uploads.append(remote)
        return {"path": remote, "filename": local_path.name}

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
        return {
            "state": self.state,
            "filename": self.current_filename,
            "progress": 1.0 if self.state == "complete" else 0.5,
            "message": None,
        }

    def move_to_tool_change_position(self) -> dict:
        self.tool_change_moves += 1
        return self.runtime.snapshot()

    def probe_tool_reference(self, *, x_mm: float, y_mm: float, probe_config: dict | None) -> dict:
        self.probe_calls += 1
        self.runtime._last_probe = {"x_mm": x_mm, "y_mm": y_mm, "z_mm": 4.5 - self.probe_calls * 0.1}
        return {"probe": dict(self.runtime._last_probe)}


class JobServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repository = JsonProjectRepository(Path(self.tempdir.name))
        self.project_service = ProjectService(self.repository)
        self.machine_session_service = MachineSessionService()
        self.reference_service = ReferenceSessionService(self.repository, None, self.machine_session_service, None)  # type: ignore[arg-type]
        self.physical_map_service = PhysicalMapService(self.repository)
        self.compensated_service = CompensatedGCodeService(self.repository, self.physical_map_service)
        self.runtime = FakeRuntime()
        self.adapter = FakeAdapter(self.runtime)
        self.job_service = JobService(
            self.repository,
            self.physical_map_service,
            self.reference_service,
            self.compensated_service,
            self.runtime,
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

    def test_job_run_executes_all_operations_with_two_tool_changes(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run = self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "WAITING_TOOL_CHANGE")
        self.assertEqual(run["operations"][0]["execution_status"], "COMPLETED")
        self.assertEqual(run["operations"][1]["execution_status"], "COMPLETED")
        self.assertEqual(self.adapter.tool_change_moves, 1)

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        run = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="measure-reference")
        self.assertIn(run["state"], {"TOOL_REFERENCE_READY", "JOB_STARTING", "WAITING_TOOL_CHANGE", "JOB_COMPLETE"})
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(run["state"], "WAITING_TOOL_CHANGE")
        self.assertEqual(run["operations"][2]["execution_status"], "COMPLETED")

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="measure-reference")
        self.job_service._threads[(self.project_id, self.setup_id, "superior")].join(timeout=5)  # type: ignore[attr-defined]
        run = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(run["state"], "JOB_COMPLETE")
        self.assertEqual(run["summary"]["operations_completed"], 4)
        self.assertEqual(self.adapter.probe_calls, 2)
        self.assertEqual(self.adapter.tool_change_moves, 2)
        self.assertEqual(len(self.adapter.started), 4)
        self.assertEqual(run["operations"][3]["execution_status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
