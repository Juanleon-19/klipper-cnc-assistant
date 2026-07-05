import curses
import time


MAX_SPEED = 20.0

UPDATE_PERIOD = 0.02
KEY_TIMEOUT = 0.15


class KeyboardState:
    def __init__(self):
        self.last_key_time = {}

    def register_key(self, key):
        self.last_key_time[key] = time.perf_counter()

    def is_active(self, key):
        last_time = self.last_key_time.get(key)

        if last_time is None:
            return False

        elapsed = time.perf_counter() - last_time

        return elapsed <= KEY_TIMEOUT


def calculate_velocity(keyboard):
    vx = 0.0
    vy = 0.0

    if keyboard.is_active(ord("a")):
        vx -= MAX_SPEED

    if keyboard.is_active(ord("d")):
        vx += MAX_SPEED

    if keyboard.is_active(ord("w")):
        vy += MAX_SPEED

    if keyboard.is_active(ord("s")):
        vy -= MAX_SPEED

    return vx, vy


def main(stdscr):
    curses.curs_set(0)

    stdscr.nodelay(True)

    keyboard = KeyboardState()

    while True:
        key = stdscr.getch()

        if key != -1:
            if key == ord("q"):
                break

            keyboard.register_key(key)

        vx, vy = calculate_velocity(keyboard)

        stdscr.erase()

        stdscr.addstr(
            0,
            0,
            "KLIPPER CNC ASSISTANT",
        )

        stdscr.addstr(
            1,
            0,
            "EXPERIMENT 005 - KEYBOARD INPUT",
        )

        stdscr.addstr(
            3,
            0,
            "W / A / S / D : Jog command",
        )

        stdscr.addstr(
            4,
            0,
            "Q             : Exit",
        )

        stdscr.addstr(
            6,
            0,
            "DRY RUN - MACHINE MOTION DISABLED",
        )

        stdscr.addstr(
            8,
            0,
            f"VX = {vx:8.3f} mm/s",
        )

        stdscr.addstr(
            9,
            0,
            f"VY = {vy:8.3f} mm/s",
        )

        if vx == 0.0 and vy == 0.0:
            state = "IDLE"

        else:
            state = "JOG REQUESTED"

        stdscr.addstr(
            11,
            0,
            f"STATE = {state}",
        )

        stdscr.refresh()

        time.sleep(UPDATE_PERIOD)


if __name__ == "__main__":
    curses.wrapper(main)
