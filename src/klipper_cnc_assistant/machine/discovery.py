from klipper_cnc_assistant.machine.state import (
    AxisLimits,
    MachinePosition,
    MachineState,
)


def discover_machine(
    moonraker_client,
):
    status = moonraker_client.query_objects(
        {
            "toolhead": [
                "position",
                "homed_axes",
                "axis_minimum",
                "axis_maximum",
                "max_velocity",
                "max_accel",
            ]
        }
    )

    toolhead = status.get("toolhead")

    if not isinstance(toolhead, dict):
        raise RuntimeError(
            "Klipper toolhead state is unavailable"
        )

    position = toolhead["position"]

    axis_minimum = toolhead["axis_minimum"]
    axis_maximum = toolhead["axis_maximum"]

    return MachineState(
        position=MachinePosition(
            x=float(position[0]),
            y=float(position[1]),
            z=float(position[2]),
        ),
        x_limits=AxisLimits(
            minimum=float(axis_minimum[0]),
            maximum=float(axis_maximum[0]),
        ),
        y_limits=AxisLimits(
            minimum=float(axis_minimum[1]),
            maximum=float(axis_maximum[1]),
        ),
        z_limits=AxisLimits(
            minimum=float(axis_minimum[2]),
            maximum=float(axis_maximum[2]),
        ),
        homed_axes=str(
            toolhead["homed_axes"]
        ),
        max_velocity=float(
            toolhead["max_velocity"]
        ),
        max_accel=float(
            toolhead["max_accel"]
        ),
    )
