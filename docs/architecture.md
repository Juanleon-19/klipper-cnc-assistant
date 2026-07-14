# Arquitectura actual

## Objetivo de esta fase

La Fase 3 transforma el MVP web en una aplicación de trabajo más compacta, clara y preparada para crecimiento, manteniendo todo el sistema en **modo simulado**.

## Capas productivas

```text
frontend/ React + TypeScript + Vite
        |
        | fetch / mismo origen en producción
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

El análisis de G-code vive en `src/klipper_cnc_assistant/gcode/` y sigue siendo estrictamente analítico: no ejecuta movimientos.

## Flujo de datos del producto actual

1. El usuario crea o edita un proyecto PCB desde la interfaz web.
2. FastAPI valida el payload y lo entrega a `ProjectService`.
3. `JsonProjectRepository` persiste el proyecto en `data/projects/<id>/project.json`.
4. El usuario selecciona operaciones concretas por proyecto.
5. El usuario carga un archivo `.nc`, `.gcode` o `.tap`.
6. El backend valida nombre, extensión, tamaño y codificación UTF-8.
7. El archivo original se conserva en `originals/` con SHA-256.
8. El analizador extrae límites, movimientos, incidencias, comandos manuales y segmentos de vista previa.
9. El frontend representa la trayectoria con `react-konva` y transforma las coordenadas solo en la capa visual.
10. El diagnóstico del sistema expone estado de API, almacenamiento y modo de máquina simulado.

## Componentes backend reutilizados y ampliados

- `domain/`: modelo de proyecto, operaciones, material y análisis.
- `application/ProjectService`: persistencia, carga segura, análisis y reglas de negocio.
- `storage/JsonProjectRepository`: almacenamiento JSON y preservación de originales.
- `gcode/analyzer.py`: análisis modal, incidencias, arcos `G2/G3`, desbordes de material y metadatos de segmento.
- `api/`: FastAPI, respuestas en español, carga multipart y entrega del frontend estático.

## Componentes frontend de esta fase

- `App.tsx`: shell principal, navegación, dashboard y rutas de vista.
- `components/ProjectWorkspace.tsx`: espacio de trabajo del proyecto.
- `features/viewer/viewerMath.ts`: matemáticas de encuadre y transformación.
- `features/viewer/ToolpathViewer.tsx`: visor canvas V2.
- `features/viewer/ViewerToolbar.tsx`: controles integrados.
- `features/viewer/ViewerInspector.tsx`: metadatos del segmento actual.
- `lib/ui.ts`: traducción centralizada de estados y reglas visuales.

## Seguridad actual

- no se llama a Moonraker desde la nueva aplicación web para mover la máquina;
- no se envía G-code a Klipper;
- no existe homing, jog, probe ni ejecución de trabajo;
- la sesión de máquina reportada es simulada;
- las acciones de husillo externo continúan marcadas como manuales;
- el frontend no muestra controles falsos de movimiento.
