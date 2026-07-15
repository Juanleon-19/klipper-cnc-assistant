# Frontend

## Stack

- React 18
- TypeScript
- Vite 5
- CSS propio responsive
- `react-konva` + `konva` para visor 2D
- `plotly.js-dist-min` para superficie 3D
- Vitest + Testing Library
- ESLint

## Fuente única de estado de máquina

La UI usa `MachineContext` (`frontend/src/context/MachineContext.tsx`) como fuente visible de estado físico. El flujo es:

```text
MachineRuntime -> /api/machine/runtime|status -> App -> MachineContext -> Sidebar/Sistema/Proyecto
```

Cuando `MachineRuntime.mode` es `PHYSICAL`, la UI normaliza el texto a `FÍSICO` aunque el backend devuelva `FISICO`. Sidebar, Sistema y workspace usan el mismo contexto y dejan de mezclar textos de sesión simulada con runtime físico.

## Navegación del producto

El producto se recorre desde el proyecto y montaje activo:

```text
Proyecto -> Montaje -> Operaciones -> Referencia -> Mapa de alturas -> Compensación -> Ejecución
```

`Sistema` queda como diagnóstico técnico: aplicación, Moonraker HTTP/WebSocket, Klipper, posición, homing, límites, Arduino, joystick, botones, sonda, seguridad, eventos, cancelación técnica y emergencia `M112`. Ya no contiene el flujo productivo principal de homing, Z segura, centro, joystick, referencia y malla.

## Referencia

En modo simulado se conserva el stepper de referencia manual. En modo físico se muestra una guía real dentro del montaje:

1. conexión y diagnóstico;
2. homing, Z segura de traslado y centro;
3. posicionamiento X/Y con joystick discreto;
4. armado de referencia;
5. sondeo por botón externo o confirmación supervisada;
6. guardado de origen X/Y y referencia Z medida para la herramienta actual.

La Z segura se etiqueta como traslado; no se confunde con referencia Z, profundidad ni contacto.

## Mapa de alturas

La pestaña tiene selector funcional:

- `SIMULADO`: configuración, simulación e importación matemática.
- `MEDIDO FÍSICAMENTE`: mapa físico del montaje/cara/revisión de colocación, referencia Z de herramienta, grid, puntos, progreso y acciones punto a punto.

Acciones visibles en `MEDIDO FÍSICAMENTE`:

- `Usar área desde operaciones / Generar malla`;
- `Iniciar sondeo` o `Continuar malla incompleta`;
- `Pausar`;
- `Reanudar`;
- `Reintentar/continuar punto`;
- `Cancelar`.

La región se calcula desde operaciones analizadas y conserva la convención:

```text
machine_x = machine_origin_x + gcode_x
machine_y = machine_origin_y + gcode_y
```

## Visor 2D

`HeightMapHeatmap.tsx` muestra ejes X/Y, ticks adaptativos, mm, rejilla mayor/menor, cursor, material, trayectoria, región, muestras y malla física. La malla se dibuja con recorrido serpentino, puntos pendientes/medidos/fallidos y selector de coordenadas `Local G-code` / `Máquina`.

La pantalla completa usa el canvas como superficie principal y mantiene controles de encuadre. En Firefox headless el API fullscreen puede denegarse; se validó visualmente el layout con captura headless de la vista fullscreen solicitada.

## Compensación

La pestaña `Compensación` permite previsualizar y generar un archivo real. El archivo se guarda en `generated/compensated/`, conserva X/Y y aplica:

```text
z_compensado = z_original + delta_superficie(x,y)
```

La descarga usa `/api/projects/{project_id}/generated/{file_path}`.

## Ejecución

La pestaña `Ejecución` expone preflight Moonraker/Klipper con checks visibles de modo físico, runtime, Klipper, homing, mapa, referencia y archivo compensado. Las acciones visibles son subir archivo, confirmar archivo, confirmar herramienta, confirmar spindle, iniciar ejecución supervisada, pausar, reanudar, cancelar y emergencia. El inicio real queda bloqueado por software hasta prueba física supervisada.

## Capturas verificadas

Las capturas de la aplicación servida por FastAPI con fixture local están en `docs/artifacts/visual-verification/`:

1. `01-sidebar-modo-fisico.png`
2. `02-referencia-fisica-montaje.png`
3. `03-malla-configurada.png`
4. `04-malla-superpuesta-visor.png`
5. `05-sondeo-progreso-fixture-medida.png`
6. `06-mapa-medido-superficie-3d.png`
7. `07-visor-ejes-ticks.png`
8. `08-pantalla-completa.png`
9. `09-compensacion.png`
10. `10-ejecucion-preflight.png`

Firefox headless no tuvo WebGL disponible para Plotly, por lo que la inspección final de superficie 3D debe repetirse en navegador normal con WebGL durante validación supervisada.

## Validación

```bash
cd frontend
npm run lint
npm run test
npm run build
```

El build final verificado generó `frontend/dist/index.html`, `assets/index-DiGQGU_B.js`, `assets/index-C_RZSt3A.css` y `assets/plotly.min-CofRTlwV.js`.


## Verificación de integración visible 2026-07-14

El frontend consume `CoordinateReference.posicion_captura` como `CapturedPosition` estructurado y lo muestra en Referencia física. En modo físico, Mapa de alturas no presenta `SIMULADO` como acción principal; muestra directamente `Mapa medido físicamente` con controles compactos, configuración física, armado e inicio de sondeo.

El visor 2D incorpora ejes X/Y, ticks en mm, rejilla mayor/menor, selector Local/Máquina, región, trayectoria, malla, recorrido serpentino, punto activo e inspector colapsable. Pantalla completa usa barra inferior e inspector flotante.

Build servido verificado por `curl`: `/assets/index-DNVlB1UT.js` y `/assets/index-yhqof53C.css`.
