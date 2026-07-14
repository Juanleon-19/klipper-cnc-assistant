# Frontend

## Stack

- React 18
- TypeScript
- Vite 5
- CSS propio responsive
- `react-konva` + `konva`
- Vitest + Testing Library
- ESLint

## Estructura principal

```text
frontend/
  src/
    components/
    features/viewer/
    lib/
    test/
    App.tsx
    main.tsx
    styles.css
```

## Vista funcional

La interfaz está completamente en español e incluye:

- barra lateral compacta y colapsable;
- encabezado superior compacto;
- etiqueta permanente `MÁQUINA EN MODO SIMULADO`;
- dashboard de trabajo;
- listado real de proyectos;
- formulario de creación y edición;
- flujo visual de operaciones por proyecto;
- carga de G-code por archivo real;
- análisis técnico por operación;
- visor técnico 2D V2 con toolbar integrada, capas, inspector y recorrido visual;
- pantalla de sistema como diagnóstico secundario.

## Visor técnico 2D V2

El visor en `features/viewer/` usa canvas vía `react-konva` y separa:

- matemáticas de encuadre y transformación;
- tema visual del visor;
- toolbar y capas;
- inspector de segmentos;
- render de trayectorias y advertencias.

Capacidades actuales:

- ajuste al material, trayectoria y todo;
- proporción 1:1 entre X/Y;
- inversión de Y solo en pantalla;
- zoom con rueda;
- desplazamiento por arrastre;
- pinch zoom táctil;
- inspector de segmento con línea, Z, avance y distancia;
- color opcional por profundidad Z;
- recorrido visual por segmento;
- cuadrícula adaptativa y capas activables.

## Desarrollo local

```bash
cd frontend
npm install
npm run dev
```

Durante desarrollo, Vite usa proxy a `http://127.0.0.1:8000/api`.

## Validación

```bash
cd frontend
npm run lint
npm run test
npm run build
```

## Limitaciones actuales

- no hay frontend 3D;
- no hay Gerber;
- no hay mapa de alturas;
- no hay ejecución ni simulación física de la máquina;
- el visor sigue siendo informativo, no una simulación exacta de mecanizado.
