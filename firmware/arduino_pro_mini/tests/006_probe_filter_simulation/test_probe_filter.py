from __future__ import annotations

import unittest


MASK32 = 0xFFFFFFFF
PROBE_TRIGGER_FILTER_MS = 20
PROBE_RELEASE_FILTER_MS = 40


class ProbeFilter:
    """Deterministic model of joystick_controller.ino updateProbeFilter()."""

    def __init__(self, *, pin_low: bool, now_ms: int = 0) -> None:
        self.candidate = pin_low
        self.filtered = pin_low
        self.candidate_since_ms = now_ms & MASK32
        self.changed_at_ms = now_ms & MASK32

    def update(self, *, pin_low: bool, now_ms: int) -> bool:
        now_ms &= MASK32
        if pin_low != self.candidate:
            self.candidate = pin_low
            self.candidate_since_ms = now_ms
        required_ms = PROBE_TRIGGER_FILTER_MS if self.candidate else PROBE_RELEASE_FILTER_MS
        if self.filtered != self.candidate and ((now_ms - self.candidate_since_ms) & MASK32) >= required_ms:
            self.filtered = self.candidate
            self.changed_at_ms = now_ms
        return self.filtered

    def packet_probe(self) -> bool:
        return self.filtered


class ProbeFilterTest(unittest.TestCase):
    def test_high_stable_starts_open(self) -> None:
        self.assertFalse(ProbeFilter(pin_low=False).packet_probe())

    def test_low_shorter_than_20ms_does_not_trigger(self) -> None:
        probe = ProbeFilter(pin_low=False)
        self.assertFalse(probe.update(pin_low=True, now_ms=0))
        self.assertFalse(probe.update(pin_low=True, now_ms=19))

    def test_low_for_20ms_triggers(self) -> None:
        probe = ProbeFilter(pin_low=False)
        probe.update(pin_low=True, now_ms=10)
        self.assertTrue(probe.update(pin_low=True, now_ms=30))

    def test_high_shorter_than_40ms_does_not_release(self) -> None:
        probe = ProbeFilter(pin_low=True)
        probe.update(pin_low=False, now_ms=10)
        self.assertTrue(probe.update(pin_low=False, now_ms=49))

    def test_high_for_40ms_releases_open(self) -> None:
        probe = ProbeFilter(pin_low=True)
        probe.update(pin_low=False, now_ms=10)
        self.assertFalse(probe.update(pin_low=False, now_ms=50))

    def test_low_bounce_does_not_trigger_false_contact(self) -> None:
        probe = ProbeFilter(pin_low=False)
        for now_ms, pin_low in ((0, True), (8, False), (14, True), (25, False), (36, True), (55, True)):
            probe.update(pin_low=pin_low, now_ms=now_ms)
        self.assertFalse(probe.packet_probe())
        self.assertTrue(probe.update(pin_low=True, now_ms=56))

    def test_high_bounce_eventually_releases(self) -> None:
        probe = ProbeFilter(pin_low=True)
        for now_ms, pin_low in ((0, False), (15, True), (25, False), (45, True), (55, False), (94, False)):
            probe.update(pin_low=pin_low, now_ms=now_ms)
        self.assertTrue(probe.packet_probe())
        self.assertFalse(probe.update(pin_low=False, now_ms=95))

    def test_open_triggered_open_repeats_twenty_times(self) -> None:
        probe = ProbeFilter(pin_low=False)
        now_ms = 0
        for _ in range(20):
            probe.update(pin_low=True, now_ms=now_ms)
            now_ms += PROBE_TRIGGER_FILTER_MS
            self.assertTrue(probe.update(pin_low=True, now_ms=now_ms))
            probe.update(pin_low=False, now_ms=now_ms)
            now_ms += PROBE_RELEASE_FILTER_MS
            self.assertFalse(probe.update(pin_low=False, now_ms=now_ms))

    def test_packet_flag_changes_false_true_false(self) -> None:
        probe = ProbeFilter(pin_low=False)
        states = [probe.packet_probe()]
        probe.update(pin_low=True, now_ms=0)
        states.append(probe.update(pin_low=True, now_ms=20))
        probe.update(pin_low=False, now_ms=21)
        states.append(probe.update(pin_low=False, now_ms=61))
        self.assertEqual(states, [False, True, False])

    def test_restart_high_starts_open(self) -> None:
        self.assertFalse(ProbeFilter(pin_low=False, now_ms=123).filtered)

    def test_restart_low_reflects_current_contact(self) -> None:
        self.assertTrue(ProbeFilter(pin_low=True, now_ms=123).filtered)

    def test_rollover_preserves_release_filter(self) -> None:
        probe = ProbeFilter(pin_low=True, now_ms=0xFFFFFFE0)
        probe.update(pin_low=False, now_ms=0xFFFFFFF0)
        self.assertTrue(probe.update(pin_low=False, now_ms=0x00000017))
        self.assertFalse(probe.update(pin_low=False, now_ms=0x00000018))


if __name__ == "__main__":
    unittest.main()
