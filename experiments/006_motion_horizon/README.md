# Experiment 006 — Continuous Jog Motion Horizon

## Objective

Investigate how continuous jog motion can remain responsive while limiting the amount of motion queued in Klipper.

The final Klipper CNC Assistant is expected to support analog joystick input.

The intended operator interaction is:

```text
Move joystick
      |
      v
Machine moves continuously

Release joystick
      |
      v
Machine stops quickly
```

A continuous jog implementation must therefore satisfy two competing requirements:

- Maintain smooth motion while the input remains active.
- Minimize stopping delay and additional displacement after input release.

---

## Background

Experiment 004 demonstrated that repeatedly sending motion segments can accumulate pending motion in Klipper.

The measured result was:

```text
Observed stopping delay:
750.62 ms

Additional displacement after release:
11.248 mm
```

Stopping command transmission does not immediately stop the machine if previously submitted G-code motion remains queued.

This experiment investigates how to explicitly control the amount of future motion submitted to Klipper.

---

## Motion Horizon Concept

For a commanded velocity `v` and a motion horizon `T`, the requested segment distance is:

```text
d = v * T
```

For example:

```text
v = 10 mm/s
T = 0.100 s
```

Therefore:

```text
d = 1.000 mm
```

The segment represents approximately 100 ms of motion at the requested velocity.

The amount of future motion already submitted to Klipper is referred to in this experiment as the **motion horizon**.

---

## Iteration 1 — Naive Time-Based Renewal

The first strategy used:

```text
Motion horizon : 100 ms
Renewal ratio  : 0.50
Renewal period : 50 ms
Test velocity  : 10 mm/s
Active time    : 1.0 s
```

A 100 ms motion segment was submitted approximately every 50 ms.

Conceptually:

```text
Time

0 ms        50 ms       100 ms      150 ms
 |            |            |            |
 v            v            v            v

[---100 ms---]
             [---100 ms---]
                          [---100 ms---]
                                       [---100 ms---]
```

The controller generated future motion faster than Klipper could consume it.

For every second of real time:

```text
20 commands/s * 0.100 s motion/command
```

produced approximately:

```text
2.0 seconds of submitted motion
```

per second of active input.

This caused pending motion to accumulate.

---

## Physical Results

### Run 1

```text
Initial X: 0.000 mm

Input release:
X = 0.044 mm
V = 10.000 mm/s

Stopping delay:
1149.06 ms

Additional displacement:
11.956 mm

Total displacement:
12.000 mm
```

### Run 2

```text
Initial X: 12.000 mm

Input release:
X = 12.400 mm
V = 10.000 mm/s

Stopping delay:
1173.32 ms

Additional displacement:
11.600 mm

Total displacement:
12.000 mm
```

### Result Summary

| Run | Stopping Delay | Additional Displacement | Total Displacement |
|---|---:|---:|---:|
| 1 | 1149.06 ms | 11.956 mm | 12.000 mm |
| 2 | 1173.32 ms | 11.600 mm | 12.000 mm |

**Result: FAILED**

The strategy produced a larger stopping delay than the previous continuous jog benchmark.

---

## Root Cause

The controller renewed motion based only on elapsed time.

It did not track how much previously submitted motion remained pending.

The observed machine position and the planned trajectory position are not equivalent while Klipper contains queued motion.

Conceptually:

```text
Observed position
X = 0.044 mm

Previously submitted motion
+1 mm
+1 mm
+1 mm
...
```

The controller only observed the physical position.

It did not maintain a representation of the future motion already submitted to Klipper.

This caused repeated trajectory extension and excessive queue accumulation.

---

## Proposed Strategy

The next iteration will maintain two distinct position concepts:

```text
Observed position
       |
       v
Physical position reported by Moonraker


Planned position
       |
       v
End position of motion already submitted
```

The queued distance can then be estimated as:

```text
queued_distance =
planned_position - observed_position
```

For a known commanded velocity:

```text
queued_time =
abs(queued_distance) / abs(velocity)
```

New motion should only be submitted when the remaining queued time falls below a configured renewal threshold.

Conceptually:

```text
OBSERVED ---------------- PLANNED
             |
             v
       queued motion
```

The objective is to maintain a bounded amount of future motion.

Target behavior:

```text
50–100 ms future motion
```

When joystick input is released, the controller stops extending the planned trajectory.

The remaining queued motion should then be bounded by the configured motion horizon.

---

## Next Step

Implement and validate a planned-motion horizon controller.

The next strategy must be compared against the same stopping metrics:

- Stopping delay.
- Additional displacement after input release.
- Total displacement.
- Final observed velocity.

The target is to substantially reduce stopping delay while maintaining continuous motion.
