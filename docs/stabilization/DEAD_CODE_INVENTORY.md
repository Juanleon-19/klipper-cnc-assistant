# Inventario de candidatos a código no utilizado

No autoriza borrar nada; evidencia basada en referencias/consumidores actuales.

| Candidato | Clase | Evidencia | Acción |
| --- | --- | --- | --- |
| `experiments/001` a `008` | TEST_ONLY | no importados por `src/`; investigación histórica | conservar aislados |
| `experiments/004_continuous_jog`, `006_motion_horizon` | DEPRECATE | sin import productivo; fuera de alcance | mantener hasta decisión |
| monitor serie `firmware/.../tests/005_serial_protocol` | TEST_ONLY | script manual, no import backend | conservar/documentar |
| copias `*.bak` firmware | UNKNOWN | no rastreadas, sin consumidor | no borrar en auditoría |
| `api.routes.execution_action` individual | MERGE | UI/API; inicio bloqueado frente a job real | unificar Fase 5 |
| `JobService` multioperación | KEEP | rutas/frontend/Moonraker | endurecer/probar |
| `MeshExecutionService` | KEEP | `execute-all`, persistencia/UI | robustecer Fase 3 |
| `execute-next` | KEEP | endpoint público | alinear garantías |
| mapas legados por operación/herramienta | DEPRECATE | lectura/migración en servicio | inventariar antes de retirar |
| `HeightMapService` simulado/importado | KEEP | API/UI sin hardware | mantener separado |
| `MoonrakerTelemetry` | KEEP | runtime lo instancia | añadir reconexión |
| `ManualJogController`/`JogController` | KEEP | frontera productiva | no duplicar reglas |

Antes de `REMOVE`: comprobar imports, rutas, frontend, datos persistidos y pruebas completas.
