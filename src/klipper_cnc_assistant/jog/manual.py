from klipper_cnc_assistant.jog.profiles import (
    JogMode,
    get_jog_profile,
)


class ManualJogController:
    def __init__(
        self,
        jog_controller,
        mode=JogMode.NORMAL,
    ):
        self.jog_controller = jog_controller
        self.mode = mode

    def set_mode(
        self,
        mode,
    ):
        get_jog_profile(mode)

        self.mode = mode

    def move(
        self,
        axis,
        direction,
    ):
        if direction not in (-1, 1):
            raise ValueError(
                "Direction must be -1 or 1"
            )

        profile = get_jog_profile(
            self.mode
        )

        distance = (
            profile.distance * direction
        )

        return self.jog_controller.move_relative(
            axis=axis,
            distance=distance,
            speed=profile.speed,
        )
