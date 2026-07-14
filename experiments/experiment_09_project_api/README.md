# Experimento 09 — Validacion de API de proyectos PCB

## Objetivo

Validar la base de produccion de `Klipper CNC Assistant` sin tocar hardware real:

- modelo de proyecto PCB;
- operaciones configurables;
- carga y analisis de G-code;
- persistencia JSON;
- API HTTP minima;
- estado de maquina simulado.

## Alcance

Este experimento consume solamente codigo de `src/klipper_cnc_assistant/`.

Incluye:

- creacion de proyecto por API;
- dos operaciones configurables;
- una operacion en cara superior;
- una operacion en cara inferior;
- carga de G-code original sin sobreescritura;
- analisis de limites, avances, profundidad e incidencias;
- consulta de sesion de maquina simulada.

Excluye:

- movimientos reales;
- Moonraker;
- serial;
- sondeo fisico;
- frontend.

## Riesgos de seguridad

No genera movimiento fisico. No envia G-code a la maquina. No importa `experiments/` desde `src/`.

## Procedimiento

```bash
source .venv/bin/activate
PYTHONPATH=src python experiments/experiment_09_project_api/run_validation.py
```

El script crea un directorio temporal persistente dentro del propio experimento (`runtime_data/`), levanta la aplicacion por `TestClient`, crea un proyecto, registra dos operaciones, carga dos archivos G-code de ejemplo y guarda el resultado en `results.json`.

## Criterios de aceptacion

- El proyecto queda persistido en JSON.
- La API responde en espanol.
- Los originales se guardan en `projects/<id>/originals/`.
- La operacion superior se analiza sin error critico.
- La operacion inferior registra cambio manual de herramienta.
- La sesion de maquina se mantiene simulada.

## Resultado y estado

Estado: `PASSED`

El experimento confirma que la base de produccion ya puede modelar proyectos, almacenar operaciones configurables, importar G-code sin modificar el original, analizar incidencias manuales y exponer todo por FastAPI sin tocar hardware.

## Archivos asociados

- `sample_top.nc`
- `sample_bottom.nc`
- `run_validation.py`
- `results.json`
