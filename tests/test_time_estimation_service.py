from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application.time_estimation_service import TimeEstimationService
from klipper_cnc_assistant.storage import JsonProjectRepository


class DummyConfig:
    moonraker_url: str | None = None
    moonraker_request_timeout_s = 1.0


class DummyRuntime:
    def __init__(self) -> None:
        self.config = DummyConfig()

    def snapshot(self) -> dict[str, object]:
        return {
            "klipper": {
                "max_velocity": 50.0,
                "max_accel": 500.0,
                "max_z_velocity": 15.0,
                "limits": {"z": {"min": 0.0, "max": 120.0}},
            }
        }


class ReadyAnalysisClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def get_analysis_status(self) -> dict[str, object]:
        return {"estimator_ready": True, "estimator_version": "v3.7.3"}

    def estimate_analysis(self, filename, estimator_config=None) -> dict[str, object]:
        return {"filename": filename, "time": 42.5, "estimator_config": estimator_config}

    def query_objects(self, _objects) -> dict[str, object]:
        return {
            "toolhead": {
                "max_velocity": 80.0,
                "max_accel": 800.0,
                "minimum_cruise_ratio": 0.5,
                "square_corner_velocity": 5.0,
                "axis_minimum": [0.0, 0.0, 0.0],
                "axis_maximum": [200.0, 200.0, 120.0],
            }
        }


class NotReadyAnalysisClient(ReadyAnalysisClient):
    def get_analysis_status(self) -> dict[str, object]:
        return {"estimator_ready": False}


class TimeEstimationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repository = JsonProjectRepository(Path(self.tempdir.name))
        self.runtime = DummyRuntime()

    def test_internal_estimator_handles_constant_speed_line(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G21\nG90\nG1 X60 F600\n")
        self.assertEqual(estimate["method"], "internal")
        self.assertGreater(estimate["estimated_time_s"], 6.0)
        self.assertEqual(estimate["unsupported_commands"], [])

    def test_internal_estimator_penalizes_corners_against_straight_path(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        straight = service.estimate_text("G21\nG90\nG1 X60 F600\n")
        corner = service.estimate_text("G21\nG90\nG1 X30 F600\nG1 Y30 F600\n")
        self.assertGreater(corner["estimated_time_s"], straight["estimated_time_s"])

    def test_internal_estimator_counts_dwell(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G21\nG90\nG1 X10 F600\nG4 P1500\n")
        self.assertGreaterEqual(estimate["dwell_time_s"], 1.5)
        self.assertGreaterEqual(estimate["estimated_time_s"], 2.5)

    def test_moonraker_analysis_is_used_when_ready(self) -> None:
        self.runtime.config.moonraker_url = "http://moonraker.local"
        service = TimeEstimationService(self.repository, self.runtime, client_factory=ReadyAnalysisClient)
        estimate = service.estimate_text("G21\nG90\nG1 X60 F600\n", remote_filename="job.gcode")
        self.assertEqual(estimate["method"], "moonraker_analysis")
        self.assertEqual(estimate["estimated_time_s"], 42.5)
        self.assertEqual(estimate["confidence"], "high")

    def test_internal_estimator_is_used_when_analysis_not_ready(self) -> None:
        self.runtime.config.moonraker_url = "http://moonraker.local"
        service = TimeEstimationService(self.repository, self.runtime, client_factory=NotReadyAnalysisClient)
        estimate = service.estimate_text("G21\nG90\nG1 X60 F600\n", remote_filename="job.gcode")
        self.assertEqual(estimate["method"], "internal")
        self.assertGreater(estimate["estimated_time_s"], 0.0)

    def test_offset_table_tracks_file_progress(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G21\nG90\nG1 X10 F600\nG1 X20 F600\n")
        table = estimate["offset_table"]
        self.assertGreaterEqual(len(table), 2)
        self.assertLess(table[0]["predicted_cumulative_seconds"], table[-1]["predicted_cumulative_seconds"])
