from __future__ import annotations

from .command_mapper import ControllerCommand


class JogInputBridge:
    """
    Connects controller commands to the JogController.
    """

    def __init__(
        self,
        jog_controller,
        distance: float = 1.0,
        speed: float = 20.0,
    ):

        self._jog = jog_controller

        self.distance = distance
        self.speed = speed

    def process(
        self,
        command: ControllerCommand,
    ):

        if command.jog_x != 0:

            self._jog.move_relative(
                axis="x",
                distance=command.jog_x * self.distance,
                speed=self.speed,
            )

        if command.jog_y != 0:

            self._jog.move_relative(
                axis="y",
                distance=command.jog_y * self.distance,
                speed=self.speed,
            )

        if command.probe_request:
            print("Probe routine requested")

        if command.probe_triggered:
            print("Probe triggered")

        if command.joystick_pressed:
            print("Joystick button pressed")
