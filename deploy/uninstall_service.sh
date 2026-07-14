#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="klipper-cnc-assistant.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"

if ! command -v sudo >/dev/null 2>&1; then
  echo "Falta el comando requerido: sudo" >&2
  exit 1
fi

sudo systemctl disable --now "$SERVICE_NAME" || true
sudo rm -f "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl reset-failed "$SERVICE_NAME" || true

echo "Servicio eliminado: $SERVICE_NAME"
