# P0-3 — Ejecución unificada de puntos de mapa

Base: `a759a86f598136623effbb3db3017cbdd3b2e24b`.
Rama: `safety/p0-3-unified-mesh-probe-2026-09-12`.

## Alcance

`execute-next` delega en `MeshExecutionService.start_next`, que inicia el mismo
worker que `start_all`, con presupuesto de un punto. `execute-all` conserva el
presupuesto ilimitado. La respuesta HTTP publica el estado de inicio; la
ejecución y su ownership pertenecen al backend, independientemente del cliente.

Ambas entradas comparten validación del contexto reanudable, ownership P0-1,
watchdog, cancelación, recuperación, cleanup, guardas serial/probe y persistencia
de resultados. La primitiva `runtime.probe_mesh_point` permanece interna al
pipeline de mapa. El endpoint no selecciona, sondea ni persiste puntos.

Tras un punto quedan `MESH_PAUSED` y el worker inactivo si quedan puntos.
La continuación requiere una acción explícita. Si era el último punto se usa
la finalización normal `MESH_COMPLETE`, antes de aplicar el presupuesto.
Los errores conservan la pausa y el contrato productivo sin retries automáticos.

No se modificaron P0-1/P0-2, el probe de referencia de herramienta (P0-4),
frontend, hardware, servicios ni datos operativos. No se integró PR #24.

## Validación

Tests focalizados en modo simulado:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_unified_mesh_probe tests.test_physical_integration tests.test_mesh_failure_recovery_hotfix tests.test_mesh_failure_recovery_guards tests.test_api -v
```

Resultado: **100/100 OK**, 22,715 segundos.
Las primeras ejecuciones focalizadas detectaron aserciones que contaban también
el intento previo de la referencia; se corrigieron para medir el incremento.

| Caso | Verificación |
| --- | --- |
| A/B | Mapa archivado rechazado con el mismo error por next y all; cero probes. |
| C | Varios puntos: next añade exactamente un intento y pausa los restantes. |
| D | Último punto: finalización normal, sin worker activo. |
| E | Cancelación durante next mediante el worker y cleanup existentes. |
| F | Recuperación pendiente y probe vivo con cleanup bloquean ambas entradas. |
| G | Ownership incompatible de trabajo bloquea ambas entradas; cero probes. |
| H | Endpoint delega en el servicio; ninguna llamada directa a la primitiva. |

También se comprueban un fallo sin retry y ownership conservado mientras el
worker continúa después del retorno de la petición.

Suite backend completa, una sola ejecución tras pasar los focalizados:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests -v
git diff --check
```

Resultado: **415/415 OK**, 69,078 segundos. `git diff --check` sin errores.
Frontend: no requerido, sin cambios.
Entrega: commit local, sin push ni merge; sin comandos físicos ni reinicios.
