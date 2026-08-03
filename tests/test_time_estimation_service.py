from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application.time_estimation_service import TimeEstimationService, _trapezoid_time
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
        self.assertEqual(estimate["offset_table"][-1]["predicted_cumulative_seconds"], 42.5)
        self.assertEqual(estimate["distribution_method"], "internal_scaled")
        self.assertIn("distribución temporal", estimate["distribution_detail"])

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

    def test_dwell_events_contribute_to_offset_table_in_order(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G4 P500\nG21\nG90\nG1 X10 F600\nG4 P1000\nG1 X20 F600\nG4 P250\n")
        table = estimate["offset_table"]
        self.assertGreaterEqual(len(table), 5)
        self.assertGreaterEqual(estimate["dwell_time_s"], 1.75)
        self.assertEqual(table[-1]["predicted_cumulative_seconds"], estimate["estimated_time_s"])
        self.assertLess(table[0]["predicted_cumulative_seconds"], table[2]["predicted_cumulative_seconds"])
        self.assertLess(table[2]["predicted_cumulative_seconds"], table[-1]["predicted_cumulative_seconds"])

    def test_parser_does_not_turn_g92_into_inherited_motion(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G21\nG90\nG1 X10 F600\nG92 X0 Y0\n")
        self.assertTrue(any("G92" in item for item in estimate["unsupported_commands"]))

    def test_parser_keeps_dwell_literal_without_motion_conversion(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("G21\nG90\nG1 X10 F600\nG4 P1000\n")
        self.assertEqual(estimate["unsupported_commands"], [])
        self.assertGreaterEqual(estimate["dwell_time_s"], 1.0)

    def test_unknown_duration_commands_are_reported_instead_of_ignored(self) -> None:
        service = TimeEstimationService(self.repository, self.runtime)
        estimate = service.estimate_text("M3 S1000\nM5\nT1\nM6\nSET_PIN VALUE=1\n")
        self.assertEqual(estimate["confidence"], "low")
        self.assertIn("M3", estimate["unknown_time_commands"])
        self.assertIn("M5", estimate["unknown_time_commands"])
        self.assertIn("T1", estimate["unknown_time_commands"])
        self.assertIn("M6", estimate["unknown_time_commands"])
        self.assertIn("SET_PIN", estimate["unknown_time_commands"])

    def test_minimum_cruise_ratio_never_inflates_virtual_distance(self) -> None:
        baseline = _trapezoid_time(10.0, 10.0, 0.0, 0.0, 100.0, 0.0)
        medium = _trapezoid_time(10.0, 10.0, 0.0, 0.0, 100.0, 0.5)
        maximum = _trapezoid_time(10.0, 10.0, 0.0, 0.0, 100.0, 1.0)
        self.assertGreater(baseline, 0.0)
        self.assertGreaterEqual(medium, baseline)
        self.assertGreaterEqual(maximum, baseline)
        self.assertLessEqual(maximum, 1.2)
