#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/impresora/klipper-cnc-assistant"
FRONTEND_DIR="$ROOT_DIR/frontend"
SERVICE_NAME="klipper-cnc-assistant.service"
SERVICE_SRC="$ROOT_DIR/deploy/systemd/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Falta el comando requerido: $1" >&2
    exit 1
  fi
}

require_path() {
  if [[ ! -e "$1" ]]; then
    echo "No existe la ruta requerida: $1" >&2
    exit 1
  fi
}

require_command sudo
require_command systemctl
require_command npm
require_command curl
require_command ss
require_path "$PYTHON_BIN"
require_path "$SERVICE_SRC"
require_path "$FRONTEND_DIR/package.json"

if ss -ltn '( sport = :8000 )' | grep -q ':8000'; then
  echo "El puerto 8000 ya esta en uso."
  ss -ltnp '( sport = :8000 )' || true
  echo "Detenga primero la instancia manual que este usando el puerto 8000 y vuelva a ejecutar este instalador."
  exit 1
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m pip install -e .
"$PYTHON_BIN" -m pip check
npm ci --prefix "$FRONTEND_DIR"
npm run build --prefix "$FRONTEND_DIR"

sudo install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Servicio instalado y respondiendo en http://127.0.0.1:8000/api/health"
    exit 0
  fi
  sleep 1
 done

 echo "El servicio no respondio a tiempo. Revise: sudo journalctl -u $SERVICE_NAME -n 100 --no-pager" >&2
 exit 1
