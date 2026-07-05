from dataclasses import dataclass
from enum import Enum


class JogMode(Enum):
    COARSE = "coarse"
    NORMAL = "normal"
    FINE = "fine"


@dataclass(frozen=True)
class JogProfile:
    distance: float
    speed: float


JOG_PROFILES = {
    JogMode.COARSE: JogProfile(
        distance=10.0,
        speed=40.0,
    ),
    JogMode.NORMAL: JogProfile(
        distance=1.0,
        speed=10.0,
    ),
    JogMode.FINE: JogProfile(
        distance=0.1,
        speed=2.0,
    ),
}


def get_jog_profile(mode):
    try:
        return JOG_PROFILES[mode]

    except KeyError as error:
        raise ValueError(
            f"Unsupported jog mode: {mode}"
        ) from error
