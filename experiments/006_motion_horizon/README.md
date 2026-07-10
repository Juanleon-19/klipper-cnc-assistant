# Experiment 006 — Continuous Jog Motion Horizon

## Objective

Investigate how continuous jog motion can remain responsive while limiting the amount of future motion submitted to Klipper.

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

A continuous jog implementation must satisfy two competing requirements:

- Maintain smooth motion while the input remains active.
- Minimize stopping delay and additional displacement after input release.

The purpose of this experiment is not to tune motion for one specific machine.

The objective is to identify a motion-control strategy that can operate above different Klipper-controlled machines while obtaining machine limits and live state from the Klipper and Moonraker interfaces.

---

## Background

Experiment 004 demonstrated that repeatedly submitting motion segments can accumulate pending motion in Klipper.

A representative result was:

```text
Observed stopping delay:
750.62 ms

Additional displacement after release:
11.248 mm
```

Stopping command transmission does not immediately stop the machine when previously submitted G-code motion remains queued.

Experiment 006 investigates how to explicitly control the amount of future motion submitted to Klipper.

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

The amount of future motion already submitted to Klipper is referred to in this experiment as the motion horizon.

The target behavior is:

```text
50–100 ms of future motion
```

The intended controller behavior is:

```text
Input active
     |
     v
Maintain a small amount
of future motion
     |
     v
Input released
     |
     v
Stop submitting motion
     |
     v
Only the bounded horizon
remains to execute
```

---

# Iteration 1 — Naive Time-Based Renewal

## Strategy

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

## Physical Results

### Run 1

```text
Initial X:
0.000 mm

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
Initial X:
12.000 mm

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

| Run | Stopping Delay | Additional Displacement | Total Displacement |
| --- | ---: | ---: | ---: |
| 1 | 1149.06 ms | 11.956 mm | 12.000 mm |
| 2 | 1173.32 ms | 11.600 mm | 12.000 mm |

## Result

```text
FAILED
```

## Root Cause

The controller renewed motion based only on elapsed wall-clock time.

It did not track how much previously submitted motion remained pending.

The controller therefore submitted trajectory faster than the physical machine consumed it.

The resulting behavior was:

```text
trajectory generation rate
          >
trajectory execution rate
```

Pending motion accumulated inside the command and motion execution pipeline.

---

# Iteration 2 — Observed-Position Motion Horizon

## Strategy

The second strategy introduced two position concepts:

```text
Observed position
       |
       v
Klipper / Moonraker reported requested trajectory position


Planned position
       |
       v
End position of motion submitted by the controller
```

The queued distance was estimated as:

```text
queued_distance =
planned_position - observed_position
```

For a known commanded velocity:

```text
queued_time =
abs(queued_distance) / abs(velocity)
```

New motion was submitted only when the estimated queued time fell below the renewal threshold.

## Physical Result

A representative result was:

```text
Velocity:
10.000 mm/s

Active time:
1.000 s

Commands sent:
2

Estimated stopping delay:
6.76 ms

Additional displacement:
1.000 mm

Total displacement:
2.000 mm

Planned final position:
6.000 mm

Observed final position:
6.000 mm

Planning error:
0.000 mm
```

## Result

```text
FAILED FOR CONTINUOUS JOG
```

## Analysis

The strategy bounded pending motion and produced excellent stopping behavior.

However, the requested displacement was approximately:

```text
10 mm/s * 1 s = 10 mm
```

The measured displacement was only:

```text
2 mm
```

The controller effectively behaved as:

```text
MOVE
  |
  v
WAIT FOR REPORTED POSITION
  |
  v
MOVE
  |
  v
WAIT
```

The strategy was position-accurate and responsive when stopping, but it did not maintain continuous motion.

This indicated that raw reported position was not updating fast enough to directly drive a 5–10 ms jog control loop.

---

# Telemetry Diagnostic

A dedicated telemetry test was performed using a single motion command:

```text
Distance:
10 mm

Velocity:
10 mm/s
```

The control program sampled `MachineState` approximately every 10 ms.

However, new `motion_report.live_position` values were observed approximately every 250 ms.

Representative samples were:

```text
t = 0.390 s    X = 0.302 mm    V = 10.000 mm/s
t = 0.649 s    X = 2.804 mm    V = 10.000 mm/s
t = 0.900 s    X = 5.313 mm    V = 10.000 mm/s
t = 1.151 s    X = 7.819 mm    V = 10.000 mm/s
t = 1.401 s    X = 10.000 mm   V = 0.000 mm/s
```

Final result:

```text
Initial X:
0.000 mm

Final X:
10.000 mm

Measured displacement:
10.000 mm
```

## Finding

`MoonrakerTelemetry` and `MachineState` were receiving valid motion information.

The telemetry layer was not broken.

However, repeated reads of `MachineState` between WebSocket updates return the most recently received telemetry sample.

Therefore:

```text
control loop frequency
        !=
telemetry update frequency
```

A control loop operating every 5–10 ms cannot interpret repeated telemetry values as new physical measurements.

The observed telemetry update interval was also longer than the desired 50–100 ms motion horizon.

This makes pure observed-position horizon control unsuitable for continuous jog.

---

# Iteration 3 — Predictive Time Horizon

## Strategy

The third strategy estimated horizon consumption using elapsed wall-clock time.

After submitting:

```text
distance = 1 mm
velocity = 10 mm/s
```

the controller assumed approximately:

```text
100 ms
```

of future motion had been submitted.

The estimated remaining horizon was reduced using elapsed time.

When the remaining horizon reached approximately 50 ms, another trajectory extension was submitted.

Conceptually:

```text
submit 100 ms motion
        |
        v
wall-clock time passes
        |
        v
estimated horizon decreases
        |
        v
remaining <= 50 ms
        |
        v
submit extension
```

## Physical Result

```text
Velocity:
10.000 mm/s

Active time:
1.000 s

Commands sent:
19

Command-to-motion latency:
1395.67 ms

Estimated stopping delay:
1654.59 ms

Additional displacement:
10.693 mm

Total displacement:
10.693 mm

Planned final position:
10.693 mm

Observed final position:
10.693 mm

Planning error:
0.000 mm
```

## Result

```text
FAILED
```

## Important Finding

The final planning error was:

```text
0.000 mm
```

The controller submitted:

```text
10.693 mm
```

and the machine eventually reached:

```text
10.693 mm
```

The planned trajectory position was therefore internally consistent.

The problem was not final position accounting.

The problem was the assumed trajectory consumption time.

## Root Cause

The predictive controller assumed that submitted motion began being consumed immediately.

Conceptually:

```text
submit segment
      |
      v
start consuming horizon immediately
```

The physical system behaved differently:

```text
submit segment
      |
      v
Moonraker request
      |
      v
Klipper command processing
      |
      v
motion planning
      |
      v
queued trajectory
      |
      v
physical execution
```

Therefore:

```text
submission time
      !=
physical execution time
```

The predictor reduced its logical horizon before the machine had physically consumed the submitted trajectory.

This caused repeated extensions.

During one test, 19 extensions were submitted while the observed position remained:

```text
X = 0.000 mm
```

The controller reconstructed the same queue accumulation problem observed in Iteration 1.

---

# Iteration 4 — Hybrid Estimated Motion Horizon

## Objective

Combine reported telemetry with estimated position between telemetry updates.

The strategy uses:

```text
reported position
reported velocity
sample time
planned position
```

The estimated live position is:

```text
estimated_position =
sample_position
+
sample_velocity * elapsed_since_sample
```

Mathematically:

```text
x_hat(t) = x_sample + v_sample * dt
```

When a new telemetry sample is detected, the estimator is corrected using the reported machine state.

Conceptually:

```text
Telemetry sample

X = 0.407 mm
V = 10 mm/s
        |
        v
State estimator
        |
        +--> estimated X
        +--> estimated X
        +--> estimated X
        |
        v
New telemetry sample
        |
        v
Estimator correction
```

The hybrid motion horizon is calculated using:

```text
queued_distance =
planned_position - estimated_position
```

and:

```text
queued_time =
abs(queued_distance) / abs(commanded_velocity)
```

New motion is submitted when:

```text
queued_time <= renewal_horizon
```

The target horizon remains:

```text
100 ms
```

and the renewal threshold remains:

```text
50 ms
```

## Physical Result

The first hybrid test produced:

```text
Initial X:
0.000 mm

Velocity:
10.000 mm/s

Active time:
1.000 s

Target horizon:
100.0 ms

Renewal horizon:
50.0 ms
```

Controller activity:

```text
Commands sent:
6

Telemetry samples observed:
2
```

Observed telemetry samples:

```text
Sample 1:
X = 0.407 mm
V = 10.000 mm/s

Sample 2:
X = 1.000 mm
V = 0.000 mm/s
```

Final result:

```text
Command-to-motion latency:
1002.16 ms

Estimated stopping delay:
414.19 ms

Additional displacement:
2.630 mm

Total displacement:
3.630 mm

Planned final position:
3.630 mm

Observed final position:
3.630 mm

Planning error:
approximately 0.000 mm

Final velocity:
0.000 mm/s
```

## Result

```text
PARTIAL IMPROVEMENT
```

The hybrid strategy substantially reduced queue accumulation compared with the predictive time horizon.

Comparison:

| Strategy | Commands | Additional Displacement | Stopping Delay |
| --- | ---: | ---: | ---: |
| Naive renewal | 8 | 11.248 mm | 750.62 ms |
| Predictive time horizon | 19 | 10.693 mm | 1654.59 ms |
| Hybrid estimated horizon | 6 | 2.630 mm | 414.19 ms |

The hybrid strategy also maintained essentially zero final planning error:

```text
planned position = 3.630 mm
observed position = 3.630 mm
```

However, the expected displacement for the requested input was approximately:

```text
10 mm/s * 1 s = 10 mm
```

The measured displacement was:

```text
3.630 mm
```

The strategy therefore still failed to maintain the requested continuous jog velocity.

---

## Hybrid Estimator Failure Mode

The critical telemetry sequence was:

```text
Sample 1:
X = 0.407 mm
V = 10.000 mm/s
```

The estimator then propagated position using:

```text
x_hat(t) = x_sample + v_sample * dt
```

This allowed several trajectory extensions to be generated.

The next detected sample was:

```text
Sample 2:
X = 1.000 mm
V = 0.000 mm/s
```

At this point, the estimator accepted:

```text
velocity = 0
```

as the new motion state.

However, previously submitted trajectory still existed and the final machine position became:

```text
X = 3.630 mm
```

This demonstrates that a zero reported `live_velocity` sample cannot automatically be interpreted as completion of all submitted jog trajectory.

Therefore:

```text
reported live_velocity = 0
```

does not necessarily mean:

```text
continuous jog trajectory complete
```

This distinction is critical for segmented jog control.

---

# Combined Experimental Findings

The four tested strategies expose different limitations.

| Strategy | Continuous Motion | Bounded Stop | Final Planning Accuracy |
| --- | --- | --- | --- |
| Naive time renewal | Yes | No | Not controlled |
| Reported-position horizon | No | Yes | High |
| Predictive time horizon | Yes | No | High |
| Hybrid estimated horizon | Partial | Improved | High |

The experiments demonstrate that neither wall-clock prediction, raw reported position, nor direct velocity extrapolation is sufficient independently.

The control architecture must distinguish between:

```text
telemetry state
planned trajectory state
submitted trajectory state
continuous jog intent
```

---

# Current Technical Conclusion

The most important result of Experiment 006 is that continuous jog cannot be implemented reliably by treating individually submitted relative G-code moves as independent real-time velocity commands.

The current `JogController` submits discrete relative motion segments.

Klipper receives and plans these segments as G-code trajectory.

The application, however, is attempting to create behavior similar to:

```text
velocity command active
        |
        v
machine moves continuously

velocity command removed
        |
        v
machine stops
```

These are different control abstractions.

The current experimental architecture is:

```text
joystick intent
      |
      v
generate short relative move
      |
      v
submit G-code
      |
      v
repeat
```

The desired abstraction is:

```text
joystick intent
      |
      v
continuous jog controller
      |
      v
bounded trajectory generation
      |
      v
Klipper motion execution
```

Experiment 006 has demonstrated that the trajectory generation layer requires explicit knowledge of submitted and consumed motion.

---

# New Finding — Klipper Internal Motion Queue Is the Correct Diagnostic Target

Further review of the official Klipper documentation shows that Klipper exposes the internal trapezoid motion queue through `motion_report/dump_trapq`.

That diagnostic path is more appropriate than continuing to infer queue state only from reported position and velocity.

The revised experimental direction is:

```text
joystick intent
      |
      v
submit motion
      |
      v
observe trapq directly
      |
      v
measure queue horizon
      |
      v
bounded continuous jog
```

The experiment now focuses on Klipper's internal motion timing and queue representation rather than further application-side estimation heuristics.

---

## Experiment Status

```text
Experiment 006:
IN PROGRESS
```

Validated findings:

```text
Naive segment renewal accumulates queued motion.

Reported-position control is responsive but discontinuous.

Reported telemetry updates significantly slower
than the application control loop.

Wall-clock horizon prediction does not represent
physical trajectory consumption correctly.

Estimated position between telemetry samples reduces
queue accumulation but still does not maintain continuous jog.

A zero reported live_velocity sample cannot automatically
be treated as completion of all submitted jog trajectory.
```

The next iteration must measure Klipper's trapq rather than continuing to guess queue state from application-side telemetry.
