from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import CompensatedGCodeService, JobService, PhysicalMapService, ProjectService, ReferenceSessionService
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
from klipper_cnc_assistant.application.services import MachineSessionService
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
        self.spindle_commands: list[str] = []

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
            "active": False,
        }

    def move_to_tool_change_position(self) -> dict:
        self.tool_change_moves += 1
        return self.runtime.snapshot()

    def probe_tool_reference(self, *, x_mm: float, y_mm: float, probe_config: dict | None) -> dict:
        self.probe_calls += 1
        self.runtime._last_probe = {"x_mm": x_mm, "y_mm": y_mm, "z_mm": 4.5 - self.probe_calls * 0.1}
        return {"probe": dict(self.runtime._last_probe)}

    def stop_spindle(self) -> dict:
        self.spindle_commands.append("M5")
        return {"command_sent": True}


class BlockingToolChangeAdapter(FakeAdapter):
    def __init__(self, runtime: FakeRuntime) -> None:
        super().__init__(runtime)
        self.entered = threading.Event()
        self.release = threading.Event()

    def move_to_tool_change_position(self) -> dict:
        self.entered.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("timeout esperando liberación de prueba")
        return super().move_to_tool_change_position()


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
        self.first_operation = self._create_operation("Fresado superior", "aislamiento", 0, "tool-vbit", "0.8", "G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.050 F120\n")
        self.second_operation = self._create_operation("Taladrado 0.8", "taladrado", 1, "tool-drill-08", "0.8 MM", "G21\nG90\nG0 X15 Y15\nG1 X15 Y15 Z-0.100 F120\n")
        self._create_measured_map()
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")

    def _create_operation(self, nombre: str, tipo: str, orden: int, tool_id: str, herramienta: str, gcode: str):
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
        return op

    def _create_measured_map(self) -> None:
        payload = self.physical_map_service.capture_reference_and_plan(
            project_id=self.project_id,
            operation_id=self.first_operation.id,
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

    def _wait_worker(self) -> None:
        thread = self.job_service._threads.get((self.project_id, self.setup_id, "superior"))  # type: ignore[attr-defined]
        if thread is not None:
            thread.join(timeout=5)

    def _save_run_state(self, state: str) -> dict:
        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run["state"] = state
        self.job_service._save_run(self.job_service._context(self.project_id, self.setup_id, "superior"), run)  # type: ignore[attr-defined]
        return run

    def test_get_run_is_read_only_and_does_not_create_current_run(self) -> None:
        run_path = self.job_service._run_file(self.job_service._context(self.project_id, self.setup_id, "superior"))  # type: ignore[attr-defined]
        self.assertFalse(run_path.exists())

        with self.assertRaisesRegex(Exception, "No existe una ejecución activa"):
            self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertFalse(run_path.exists())

    def test_prepare_run_reuses_existing_nonterminal_states(self) -> None:
        protected_states = {
            "JOB_VALIDATING",
            "READY_TO_START",
            "RUNNING",
            "SPINDLE_STOP_REQUIRED",
            "TOOL_CHANGE_REQUIRED",
            "WAITING_TOOL_REFERENCE",
            "READY_TO_RESUME",
            "RECOVERY_REQUIRED",
        }
        for state in protected_states:
            with self.subTest(state=state):
                original = self._save_run_state(state)
                prepared = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
                persisted = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
                self.assertEqual(prepared["run_id"], original["run_id"])
                self.assertEqual(persisted["run_id"], original["run_id"])
                if state not in {"JOB_VALIDATING", "READY_TO_START"}:
                    self.assertEqual(prepared["state"], state)

    def test_two_operation_flow_requires_manual_spindle_stop_and_reference(self) -> None:
        prepared = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        run_id = prepared["run_id"]

        started = self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(started["run_id"], run_id)
        self._wait_worker()

        waiting_spindle = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(waiting_spindle["state"], "SPINDLE_STOP_REQUIRED")
        self.assertEqual(waiting_spindle["run_id"], run_id)
        self.assertEqual(waiting_spindle["summary"]["operations_completed"], 1)
        self.assertEqual(waiting_spindle["operations"][0]["execution_status"], "COMPLETED")
        self.assertEqual(waiting_spindle["operations"][1]["execution_status"], "PENDING")

        tool_change_required = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.assertEqual(tool_change_required["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(tool_change_required["run_id"], run_id)
        self.assertEqual(self.adapter.tool_change_moves, 1)

        waiting_reference = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        self.assertEqual(waiting_reference["state"], "WAITING_TOOL_REFERENCE")
        self.assertEqual(waiting_reference["run_id"], run_id)
        self.assertEqual(waiting_reference["operations"][1]["reference_status"], "REQUIERE_REFERENCIA")
        self.assertEqual(len(self.adapter.started), 1)

        ready_resume = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="measure-reference")
        self.assertEqual(ready_resume["state"], "READY_TO_RESUME")
        self.assertEqual(ready_resume["run_id"], run_id)
        self.assertEqual(ready_resume["summary"]["tool_changes_completed"], 1)
        self.assertEqual(ready_resume["operations"][1]["reference_status"], "LISTA")

        resumed = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="resume")
        self.assertEqual(resumed["state"], "RUNNING")
        self.assertEqual(resumed["run_id"], run_id)
        self._wait_worker()

        completed = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(completed["state"], "COMPLETED")
        self.assertEqual(completed["run_id"], run_id)
        self.assertEqual(completed["summary"]["operations_completed"], 2)
        self.assertEqual(completed["operations"][0]["execution_status"], "COMPLETED")
        self.assertEqual(completed["operations"][1]["execution_status"], "COMPLETED")
        self.assertEqual(len(self.adapter.started), 2)
        self.assertEqual(self.adapter.probe_calls, 1)
        self.assertEqual(self.adapter.spindle_commands, [])

    def test_concurrent_confirm_spindle_stopped_is_serialized(self) -> None:
        blocking_adapter = BlockingToolChangeAdapter(self.runtime)
        service = JobService(
            self.repository,
            self.physical_map_service,
            self.reference_service,
            self.compensated_service,
            self.runtime,
            adapter_factory=lambda runtime: blocking_adapter,
        )
        service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        thread = service._threads.get((self.project_id, self.setup_id, "superior"))  # type: ignore[attr-defined]
        if thread is not None:
            thread.join(timeout=5)

        results: list[dict] = []

        def worker() -> None:
            results.append(service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped"))

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(blocking_adapter.entered.wait(timeout=1.0))
        second.start()
        blocking_adapter.release.set()
        first.join(timeout=2.0)
        second.join(timeout=2.0)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["state"] == "TOOL_CHANGE_REQUIRED" for item in results))
        self.assertEqual(blocking_adapter.tool_change_moves, 1)

    def test_manual_actions_are_idempotent_under_double_click(self) -> None:
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self._wait_worker()

        first = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        second = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        self.assertEqual(first["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(second["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(self.adapter.tool_change_moves, 1)

        first = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        second = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-tool-change")
        self.assertEqual(first["state"], "WAITING_TOOL_REFERENCE")
        self.assertEqual(second["state"], "WAITING_TOOL_REFERENCE")

        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="measure-reference")
        first = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="resume")
        second = self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="resume")
        self.assertEqual(first["state"], "RUNNING")
        self.assertEqual(second["state"], "RUNNING")
        self._wait_worker()
        self.assertEqual(len(self.adapter.started), 2)

    def test_persistence_restores_same_run_id_in_tool_change_required(self) -> None:
        self.job_service.start_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self._wait_worker()
        self.job_service.run_action(project_id=self.project_id, setup_id=self.setup_id, face="superior", action="confirm-spindle-stopped")
        current = self.job_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(current["state"], "TOOL_CHANGE_REQUIRED")

        restored_service = JobService(
            self.repository,
            self.physical_map_service,
            self.reference_service,
            self.compensated_service,
            self.runtime,
            adapter_factory=lambda runtime: self.adapter,
        )
        restored = restored_service.get_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(restored["run_id"], current["run_id"])
        self.assertEqual(restored["state"], "TOOL_CHANGE_REQUIRED")
        self.assertEqual(restored["current_operation_index"], current["current_operation_index"])
        self.assertEqual(restored["summary"]["operations_completed"], current["summary"]["operations_completed"])
        self.assertEqual(restored["next_action"], current["next_action"])

    def test_prepare_run_replaces_stale_archived_or_legacy_cancelled_runs(self) -> None:
        for state in {"STALE_RUN_ARCHIVED", "JOB_CANCELLED", "JOB_CANCELED"}:
            with self.subTest(state=state):
                original = self._save_run_state(state)
                original["available_actions"] = []
                original["next_action"] = "Trabajo cancelado"
                self.job_service._save_run(self.job_service._context(self.project_id, self.setup_id, "superior"), original)  # type: ignore[attr-defined]

                prepared = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

                self.assertNotEqual(prepared["run_id"], original["run_id"])
                self.assertIn(prepared["state"], {"JOB_VALIDATING", "READY_TO_START"})
                self.assertIn(prepared["next_action"], {"Resolver bloqueos", "Iniciar trabajo"})

    def test_plan_keeps_tools_distinct_by_tool_id(self) -> None:
        plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.assertEqual(plan["summary"]["distinct_tools"], 2)
        self.assertTrue(plan["operations"][1]["tool_changed"])
        self.assertNotEqual(plan["operations"][0]["tool_id"], plan["operations"][1]["tool_id"])


if __name__ == "__main__":
    unittest.main()
