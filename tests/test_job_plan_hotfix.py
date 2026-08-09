from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import CompensatedGCodeService, PhysicalMapService, ProjectService, ReferenceSessionService
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
from klipper_cnc_assistant.application.services import MachineSessionService
from klipper_cnc_assistant.execution import JobService
from klipper_cnc_assistant.storage import JsonProjectRepository


class FakeRuntime:
    def __init__(self) -> None:
        self.config = type(
            "Config",
            (),
            {
                "moonraker_url": "http://moonraker.local",
                "moonraker_request_timeout_s": 2.0,
                "spindle_control_mode": "manual",
            },
        )()
        self.snapshot_payload = {
            "mode": "PHYSICAL",
            "moonraker": {
                "http_connected": True,
                "websocket_connected": True,
                "telemetry_state": "LIVE",
            },
            "klipper": {"ready": True, "homed_axes": "xyz"},
        }

    def snapshot(self) -> dict[str, object]:
        return json.loads(json.dumps(self.snapshot_payload))


class RecordingTimeEstimationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def estimate_text(self, _text: str) -> dict[str, object]:
        return {
            "estimated_time_s": 30.0,
            "method": "internal",
            "confidence": "medium",
            "distribution_detail": "Estimador interno.",
            "unsupported_commands": [],
        }

    def estimate_project_file(self, *, project_id: str, relative_path: str, remote_filename: str | None = None) -> dict[str, object]:
        self.calls.append(
            {
                "project_id": project_id,
                "relative_path": relative_path,
                "remote_filename": remote_filename,
            }
        )
        return {
            "estimated_time_s": 40.0,
            "method": "internal",
            "confidence": "medium",
            "distribution_detail": "Estimador interno.",
            "offset_table": [
                {"file_byte_offset": 0.0, "predicted_cumulative_seconds": 0.0},
                {"file_byte_offset": 10.0, "predicted_cumulative_seconds": 40.0},
            ],
        }


class JobPlanHotfixTest(unittest.TestCase):
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
        self.job_service = JobService(
            self.repository,
            self.physical_map_service,
            self.reference_service,
            self.compensated_service,
            self.runtime,
            time_estimation_service=self.time_estimation_service,
        )
        self.project = self.project_service.create_project(nombre="PCB hotfix", ancho_mm=80.0, alto_mm=60.0)
        self.project_id = self.project.id
        self.setup_id = self.project.montajes[0].id
        self.operation_id = self._create_operation()
        self._create_measured_map()

    def _create_operation(self) -> str:
        operation = self.project_service.add_operation(
            project_id=self.project_id,
            nombre="Fresado superior",
            tipo="aislamiento",
            cara="superior",
            orden=0,
            setup_id=self.setup_id,
            tool_id="vbit-30",
            herramienta="V-bit 30°",
        )
        self.project_service.upload_operation_gcode(
            project_id=self.project_id,
            operation_id=operation.id,
            filename=f"{operation.id}.gcode",
            content="G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.050 F120\n",
        )
        self.project_service.analyze_operation(project_id=self.project_id, operation_id=operation.id)
        return operation.id

    def _create_measured_map(self) -> None:
        operation = self.project_service.get_project(self.project_id).operations_for_setup(self.setup_id)[0]
        payload = self.physical_map_service.capture_reference_and_plan(
            project_id=self.project_id,
            operation_id=operation.id,
            machine_origin_x=100.0,
            machine_origin_y=100.0,
            reference_z=5.0,
            machine_position={"x_mm": 100.0, "y_mm": 100.0, "z_mm": 5.0},
            homed_axes="xyz",
            machine_label="moonraker-test",
            session_id="session-1",
            config=PhysicalMeshConfig(
                rows=2,
                columns=2,
                edge_margin_left_mm=2.0,
                edge_margin_right_mm=2.0,
                edge_margin_bottom_mm=2.0,
                edge_margin_top_mm=2.0,
            ),
        )
        for index, z in enumerate((5.0, 5.01, 5.02, 5.01, 4.99)):
            self.physical_map_service.record_point(
                project_id=self.project_id,
                map_id=payload["map_id"],
                point_index=index,
                z_measured=z,
                attempts=1,
                duration_s=0.1,
            )

    def _generated_metadata_file(self) -> Path:
        plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        metadata_path = plan["operations"][0]["generated_metadata_path"]
        self.assertIsNotNone(metadata_path)
        return self.repository.project_dir(self.project_id) / str(metadata_path)

    def test_build_plan_does_not_call_estimate_project_file(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.time_estimation_service.calls.clear()

        plan = self.job_service._build_plan(self.job_service._context(self.project_id, self.setup_id, "superior"))

        self.assertEqual(self.time_estimation_service.calls, [])
        self.assertIsNone(plan["operations"][0]["original_time_estimate"])

    def test_get_plan_does_not_call_estimate_project_file(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.time_estimation_service.calls.clear()

        plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(self.time_estimation_service.calls, [])
        self.assertEqual(plan["operations"][0]["generated_file_name"], Path(str(plan["operations"][0]["generated_file"])).name)

    def test_prepare_run_does_not_call_estimate_project_file(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        self.time_estimation_service.calls.clear()

        run = self.job_service.prepare_run(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(self.time_estimation_service.calls, [])
        self.assertIn(run["state"], {"JOB_READY", "JOB_VALIDATING"})

    def test_plan_still_builds_when_stored_time_estimate_is_missing(self) -> None:
        self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        metadata_file = self._generated_metadata_file()
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        metadata.pop("time_estimate", None)
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        self.time_estimation_service.calls.clear()

        plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(self.time_estimation_service.calls, [])
        self.assertIsNotNone(plan["operations"][0]["generated_file"])
        self.assertIsNone(plan["operations"][0]["estimated_time_s"])
        self.assertIsNone(plan["operations"][0]["time_estimate"])

    def test_plan_reuses_persisted_generated_time_estimate_without_recalculation(self) -> None:
        generated_plan = self.job_service.generate_project_compensation(project_id=self.project_id, setup_id=self.setup_id, face="superior")
        persisted_estimate = generated_plan["operations"][0]["estimated_time_s"]
        self.assertEqual(persisted_estimate, 40.0)
        self.time_estimation_service.calls.clear()

        refreshed_plan = self.job_service.get_plan(project_id=self.project_id, setup_id=self.setup_id, face="superior")

        self.assertEqual(self.time_estimation_service.calls, [])
        self.assertEqual(refreshed_plan["operations"][0]["estimated_time_s"], persisted_estimate)
        self.assertEqual(refreshed_plan["operations"][0]["time_estimate"]["estimated_time_s"], persisted_estimate)


if __name__ == "__main__":
    unittest.main()
