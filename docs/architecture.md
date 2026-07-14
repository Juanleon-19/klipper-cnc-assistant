# Arquitectura actual

## Objetivo de esta fase

La Fase 2 implementa una aplicacion web remota MVP para gestionar proyectos PCB, analizar G-code y visualizar trayectorias 2D sin tocar hardware real.

Todo el sistema permanece en **modo simulado**.

## Capas productivas

```text
frontend/ React + TypeScript + Vite
        |
        | fetch / mismo origen en produccion
        v
src/klipper_cnc_assistant/api
        |
        v
src/klipper_cnc_assistant/application
        |
        v
src/klipper_cnc_assistant/domain
        |
        v
src/klipper_cnc_assistant/storage
```

El analisis de G-code vive en `src/klipper_cnc_assistant/gcode/` y no ejecuta ningun movimiento.

## Flujo de datos del MVP web

1. El usuario crea o edita un proyecto PCB desde la interfaz web.
2. FastAPI valida el payload y lo entrega a `ProjectService`.
3. `JsonProjectRepository` persiste el proyecto en `data/projects/<id>/project.json`.
4. El usuario selecciona operaciones por proyecto.
5. El usuario carga un archivo `.nc`, `.gcode` o `.tap`.
6. El backend valida nombre, extension, tamano y codificacion UTF-8.
7. El archivo original se conserva en `originals/` con SHA-256.
8. El analizador extrae limites, movimientos, incidencias, comandos manuales y segmentos lineales G0/G1.
9. El frontend dibuja una vista previa 2D informativa mediante SVG.
10. El diagnostico del sistema expone estado de API, almacenamiento y modo de maquina simulado.

## Componentes backend reutilizados

- `domain/`: modelo de proyecto, operaciones, material y analisis.
- `application/ProjectService`: persistencia, carga segura, analisis y reglas de negocio.
- `storage/JsonProjectRepository`: almacenamiento JSON y preservacion de originales.
- `gcode/analyzer.py`: analisis inicial de trayectorias, incidencias y soporte geometrico parcial.
- `api/`: FastAPI, respuestas en espanol, carga multipart y entrega del frontend estatico.

## Componentes nuevos de esta fase

- `api/system/info` y `api/health` extendidos.
- soporte `multipart/form-data` para carga real de G-code.
- segmentos lineales para vista previa 2D.
- frontend SPA servido por FastAPI en produccion.
- scripts de despliegue `deploy/` para systemd.

## Seguridad actual

- no se llama a Moonraker desde la nueva aplicacion web;
- no se envia G-code a Klipper;
- no existe homing, jog, probe ni ejecucion de trabajo;
- la sesion de maquina reportada es simulada;
- G2/G3 siguen marcados como soporte incompleto;
- el frontend no muestra controles falsos de movimiento.
