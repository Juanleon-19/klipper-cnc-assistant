from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService
from klipper_cnc_assistant.input.serial_driver import HEADER, SerialDriver
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry
from klipper_cnc_assistant.storage import JsonProjectRepository
from klipper_cnc_assistant.application.services import ProjectService


def packet(direction: int, flags: int = 0, x: int = 512, y: int = 512) -> bytes:
    raw = bytes([
        HEADER,
        direction,
        flags,
        x & 0xFF,
        (x >> 8) & 0xFF,
        y & 0xFF,
        (y >> 8) & 0xFF,
    ])
    checksum = 0
    for value in raw[:7]:
        checksum ^= value
    return raw + bytes([checksum])


class FakeSerial:
    def __init__(self, *_, **__):
        self.is_open = True
        bad = bytearray(packet(4, flags=0x03))
        bad[-1] ^= 0xFF
        self.buffer = bytearray(b"noise" + bytes(bad) + packet(3, flags=0x04, x=300, y=700))

    def read(self, size: int) -> bytes:
        if not self.buffer:
            return b""
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


class PhysicalIntegrationTest(unittest.TestCase):
    def test_serial_driver_resynchronizes_and_reports_diagnostics(self) -> None:
        with patch("klipper_cnc_assistant.input.serial_driver.serial.Serial", FakeSerial):
            driver = SerialDriver(startup_delay=0)
            received = driver.read_packet()
        self.assertEqual(received.direction, "LEFT")
        self.assertTrue(received.probe)
        diagnostics = driver.diagnostics.snapshot()
        self.assertGreaterEqual(diagnostics["sync_drops"], 5)
        self.assertEqual(diagnostics["checksum_errors"], 1)
        self.assertEqual(diagnostics["valid_packets"], 1)
        self.assertEqual(diagnostics["packets_complete"], 2)

    def test_telemetry_updates_toolhead_homed_axes(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 50),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
        )
        telemetry = MoonrakerTelemetry("ws://example", machine)
        telemetry._process_message({
            "method": "notify_status_update",
            "params": [{
                "toolhead": {
                    "position": [1, 2, 3],
                    "homed_axes": "xyz",
                    "axis_minimum": [-5, -6, 0],
                    "axis_maximum": [120, 130, 60],
                    "max_velocity": 150,
                    "max_accel": 800,
                }
            }],
        })
        self.assertEqual(machine.homed_axes, "xyz")
        self.assertTrue(machine.is_homed)
        self.assertEqual(machine.x_limits.minimum, -5)
        self.assertEqual(machine.position.z, 3)

    def test_physical_map_is_keyed_by_tool_and_persists_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=50, alto_mm=40, espesor_mm=1.6)
            first = project_service.add_operation(project_id=project.id, nombre="Aislamiento 0.2", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-02", herramienta="V-bit 0.2 mm")
            second = project_service.add_operation(project_id=project.id, nombre="Corte 1.0", tipo="corte exterior", cara="superior", orden=1, tool_id="tool-10", herramienta="Fresa 1.0 mm")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=first.id, filename="first.nc", content="G21\nG90\nG1 X2 Y3 F120\nG1 X20 Y12 F120\n")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=second.id, filename="second.nc", content="G21\nG90\nG1 X30 Y5 F120\nG1 X40 Y20 F120\n")
            project_service.analyze_operation(project.id, first.id)
            project_service.analyze_operation(project.id, second.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=first.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.23,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.23},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
            )
            self.assertEqual(plan["tool_id"], "tool-02")
            self.assertEqual(plan["operation_ids"], [first.id])
            self.assertEqual(plan["source"], "MEASURED")
            self.assertTrue(plan["map_id"].startswith("measured/setup-main/tool-02/"))
            updated = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=0, z_measured=1.2)
            self.assertEqual(updated["points"][0]["status"], "MEASURED")
            self.assertEqual(updated["height_map"]["fuente_datos"], "measured")


if __name__ == "__main__":
    unittest.main()
