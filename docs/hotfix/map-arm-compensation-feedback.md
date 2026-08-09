# Map arming and compensation feedback hotfix

## Objetivo

Estabilizar la experiencia de usuario de dos operaciones síncronas que pueden tardar varios segundos sin cambiar la lógica física ni el algoritmo de compensación:

1. `3. Armar sondeo`.
2. `Generar compensación del proyecto`.

La rama de trabajo es `fix/map-arm-and-compensation-feedback` y parte de `39e8ea00316a6799f820f9c6fe431d95d9e0dff3` (main con PR #13 integrado).

## Contrato de Armar sondeo

- La preview física sigue siendo pura y no persiste `map_id`, `active_map_id`, versión ni historial.
- El mapa persistente solo se crea desde `physical-map/plan-from-reference`.
- El armado debe ser single-flight: una interacción válida produce una única petición.
- Mientras está pendiente, la UI muestra `Armando sondeo…` y bloquea acciones incompatibles.
- No hay retry automático.
- La comparación preview vs mapa persistido y sus fingerprints se conserva intacta.
- Armar no inicia el sondeo físico.
- La respuesta HTTP del armado expondrá tiempos de servidor y total para diagnosticar la demora sin optimizaciones especulativas.

## Contrato de compensación

- `Generar compensación del proyecto` tendrá busy state propio e independiente de `referenceBusy`.
- Mientras está pendiente mostrará `Generando compensación…`.
- Debe ser single-flight y no tener retry automático.
- No se modifica legacy, adaptive_fast, interpolación, subdivisión, fingerprints, referencias por herramienta, JobRun ni upload Moonraker.

## Instrumentación

La preview ya expone `preview_backend_duration_ms` y el frontend mide `preview_request_duration_ms`.

El armado añadirá, solo como telemetría de respuesta:

- `arm_backend_duration_ms`.
- `arm_request_duration_ms`.
- `arm_point_count` cuando sea consistente con el payload.

Estos campos no deben persistirse innecesariamente en el mapa.

## Performance

Primero se instrumenta. Solo se permite optimizar `PhysicalMapService.plan_from_saved_reference()` si aparece una redundancia demostrable y semánticamente neutra, cubierta por tests. No se hará refactor amplio de runtime, mapa o persistencia en este PR.

## Seguridad

Este hotfix no autoriza ni ejecuta:

- movimiento físico;
- homing;
- jog;
- probe;
- G-code;
- deploy;
- reinicio de servicios;
- cambios de Klipper o Moonraker.

## Validación requerida

- backend safe suite completa;
- frontend lint;
- frontend tests completos;
- frontend production build;
- `git diff --check`;
- tests explícitos de single-flight, resolve/reject y conservación del bloqueo preview vs mapa persistido.
