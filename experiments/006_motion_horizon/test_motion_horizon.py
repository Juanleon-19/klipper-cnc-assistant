from klipper_cnc_assistant.jog.horizon import (
    MotionHorizon,
)


def print_state(
    horizon,
    axis,
    observed_position,
    velocity,
):
    planned_position = (
        horizon.get_planned_position(axis)
    )

    queued_distance = (
        horizon.queued_distance(
            axis,
            observed_position,
        )
    )

    queued_time = (
        horizon.queued_time(
            axis,
            observed_position,
            velocity,
        )
    )

    needs_extension = (
        horizon.needs_extension(
            axis,
            observed_position,
            velocity,
        )
    )

    extension = (
        horizon.calculate_extension(
            axis,
            observed_position,
            velocity,
        )
    )

    print(
        f"Observed position : "
        f"{observed_position:.3f} mm"
    )

    print(
        f"Planned position  : "
        f"{planned_position:.3f} mm"
    )

    print(
        f"Queued distance   : "
        f"{queued_distance:.3f} mm"
    )

    print(
        f"Queued time       : "
        f"{queued_time * 1000:.3f} ms"
    )

    print(
        f"Needs extension   : "
        f"{needs_extension}"
    )

    print(
        f"Extension         : "
        f"{extension:.3f} mm"
    )


def main():
    horizon = MotionHorizon(
        target_time=0.100,
        renewal_time=0.050,
    )

    axis = "x"
    velocity = 10.0

    print("=" * 60)
    print("EXPERIMENT 006")
    print("MOTION HORIZON LOGIC TEST")
    print("=" * 60)

    print("\n[INITIALIZATION]")

    horizon.reset_axis(
        axis,
        observed_position=20.0,
    )

    print_state(
        horizon,
        axis,
        observed_position=20.0,
        velocity=velocity,
    )

    print("\n[FIRST EXTENSION]")

    extension = (
        horizon.calculate_extension(
            axis,
            observed_position=20.0,
            velocity=velocity,
        )
    )

    horizon.register_extension(
        axis,
        extension,
    )

    print_state(
        horizon,
        axis,
        observed_position=20.0,
        velocity=velocity,
    )

    print("\n[MACHINE MOVED TO X=20.400]")

    print_state(
        horizon,
        axis,
        observed_position=20.4,
        velocity=velocity,
    )

    print("\n[MACHINE MOVED TO X=20.600]")

    extension = (
        horizon.calculate_extension(
            axis,
            observed_position=20.6,
            velocity=velocity,
        )
    )

    print_state(
        horizon,
        axis,
        observed_position=20.6,
        velocity=velocity,
    )

    print("\n[REGISTER RENEWAL]")

    horizon.register_extension(
        axis,
        extension,
    )

    print_state(
        horizon,
        axis,
        observed_position=20.6,
        velocity=velocity,
    )


if __name__ == "__main__":
    main()
