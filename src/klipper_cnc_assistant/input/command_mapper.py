from __future__ import annotations

from dataclasses import dataclass

from .serial_driver import ControllerPacket


@dataclass(frozen=True)
class ControllerCommand:
    """
    High-level command generated from the handheld controller.
    """

    jog_x: int = 0
    jog_y: int = 0

    joystick_pressed: bool = False

    probe_request: bool = False

    probe_triggered: bool = False


class CommandMapper:

    def map(
        self,
        packet: ControllerPacket,
    ) -> ControllerCommand:

        jog_x = 0
        jog_y = 0

        match packet.direction:

            case "LEFT":
                jog_x = -1

            case "RIGHT":
                jog_x = 1

            case "UP":
                jog_y = 1

            case "DOWN":
                jog_y = -1

            case "UP_LEFT":
                jog_x = -1
                jog_y = 1

            case "UP_RIGHT":
                jog_x = 1
                jog_y = 1

            case "DOWN_LEFT":
                jog_x = -1
                jog_y = -1

            case "DOWN_RIGHT":
                jog_x = 1
                jog_y = -1

        return ControllerCommand(
            jog_x=jog_x,
            jog_y=jog_y,
            joystick_pressed=packet.joystick_button,
            probe_request=packet.external_button,
            probe_triggered=packet.probe,
        )
