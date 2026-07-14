# Frontend

## Stack

- React 18
- TypeScript
- Vite 5
- CSS propio responsive
- `react-konva` + `konva`
- `plotly.js-dist-min` para superficie 3D
- Vitest + Testing Library
- ESLint

## Estructura principal

```text
frontend/
  src/
    components/
    features/heightmap/
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
- workspace por pestañas internas: Archivo, Trayectoria, Referencia, Mapa de alturas y Validación;
- carga de G-code por archivo real;
- análisis técnico por operación con aviso de análisis desactualizado;
- flujo de referencia exclusivamente simulado con stepper de seis pasos;
- mapa de alturas con subpestañas `Mapa 2D`, `Superficie 3D`, `Puntos` y `Configuración`;
- previsualización matemática de compensación sin acciones de ejecución;
- pantalla de sistema como diagnóstico secundario.

## Workspace de operación

`components/ProjectWorkspace.tsx` concentra la navegación de la operación y muestra una sola sección principal a la vez.

Resumen fijo:

- proyecto activo;
- operación activa;
- estado de análisis;
- estado del mapa;
- estado de referencia simulada.

Reglas de UX principales:

- solo hay una acción principal destacada por vista;
- las acciones secundarias se agrupan en pestañas, menús o acordeones;
- las advertencias del G-code no se repiten dentro de la vista del mapa;
- cuando el análisis persistido es antiguo se muestra `Este análisis está desactualizado` con `Volver a analizar`.

## Referencia simulada

La vista `Referencia` muestra un stepper con:

1. Referencia de máquina
2. Origen de trabajo X/Y
3. Referencia Z
4. Región sondeable
5. Mapa
6. Validación

Todos los botones de avance usan el texto `Confirmar en simulación`.

La UI expone visualmente:

- coordenadas de máquina;
- origen del material;
- origen del G-code;
- punto de referencia Z;
- estado y fecha de cada confirmación.

No hay botones de homing real, jog, probe físico ni control de Moonraker.

## Mapa de alturas

La vista del mapa distingue:

- límites del material bruto;
- región sondeable interior (`probe_region`);
- trayectoria ocupada por el G-code;
- zonas excluidas;
- dominio interpolable;
- muestras simuladas o importadas;
- superficie interpolada.

### Configuración

`HeightMapControlPanel.tsx` permite definir:

- filas y columnas;
- región sondeable interior;
- zonas excluidas;
- superficie simulada;
- repetición de simulación.

`Superficie simulada` y `Repetición de simulación` viven en el acordeón `Opciones avanzadas de simulación` con ayuda textual. Esos campos se ocultan cuando la fuente del mapa es importada o real.

### Mapa 2D

`HeightMapHeatmap.tsx` muestra:

- ejes X/Y con graduación y mm;
- cursor en cruz;
- tooltip X/Y/Z;
- contorno del material;
- región sondeable;
- zonas excluidas;
- muestras diferenciadas de la interpolación;
- marcadores de mínimo, máximo y valores atípicos;
- leyenda clara y responsive.

### Superficie 3D

`HeightMapSurface3D.tsx` ofrece:

- ejes `X (mm)`, `Y (mm)` y `Z (mm)`;
- vista superior;
- vista isométrica;
- restablecer cámara;
- escala Z exagerada como estado inicial;
- cambio a escala Z real;
- factor de exageración visible sin alterar los valores reales en etiquetas ni tooltips.

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

- la referencia sigue siendo solo simulada;
- no hay exportación de G-code compensado ejecutable;
- no hay comandos de movimiento ni integración de control seguro;
- la superficie 3D es una visualización del mapa, no una simulación física del mecanizado.
