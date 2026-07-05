# Experiment 001 — Moonraker HTTP Connection

## Objective

Validate HTTP communication between Klipper CNC Assistant and a Moonraker instance.

## Scope

This experiment performs read-only operations.

No G-code commands are sent and no machine motion is requested.

## Validated operations

- Connect to the Moonraker HTTP API.
- Query Moonraker server information.
- Detect the Klipper connection state.
- Detect the Klipper operational state.
- Read the G-code position.
- Read the toolhead position.

## Moonraker endpoints

- `/server/info`
- `/printer/objects/query`

## Requirements

- Python 3.12
- `requests`
- A running Moonraker instance
- Klipper in the `ready` state

## Run

Activate the project virtual environment:

```bash
source .venv/bin/activate
