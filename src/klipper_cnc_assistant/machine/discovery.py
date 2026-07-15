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
            ],
            "configfile": [
                "settings",
            ],
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

    configfile = status.get("configfile")
    settings = configfile.get("settings") if isinstance(configfile, dict) else {}
    printer_settings = settings.get("printer") if isinstance(settings, dict) else {}
    raw_max_z_velocity = printer_settings.get("max_z_velocity") if isinstance(printer_settings, dict) else None
    max_z_velocity = None if raw_max_z_velocity is None else float(raw_max_z_velocity)

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
        max_z_velocity=max_z_velocity,
    )
