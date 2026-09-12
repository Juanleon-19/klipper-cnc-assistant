# Cierre de P0-1: ownership físico y frescura

Fecha: 2026-09-12.
Rama: `safety/p0-1-physical-ownership-freshness-2026-09-12`.
Base: `389521c`.

## Alcance cerrado

La rama contenía cambios locales para compartir permisos físicos entre runtime,
malla y ejecución; excluir procesos mediante un lock por identidad de máquina;
y comprobar sesión, homing, estado Klippy y frescura del frame antes de emitir.
Diff revisado y alcance P0-1 completo para commit local. No se modificó código
durante el cierre; se conserva la validación de 378 pruebas sin repetirla.

Los cambios de `job_service.py` se limitan al wiring del coordinador físico:
adquirir, propagar y validar permisos, conservar ownership ante incertidumbre,
cerrar productores y usar el cliente compartido del runtime bajo autorización.
No se modificó la persistencia de JobRun.

No se implementaron `JobRunStore`, CAS de JobRun, cancel dominance ni
`PrintIdentity`. El upload conserva `print_file=True`; no se introdujo el flujo
upload sin inicio seguido de start. La revisión de cancelación y recuperación
de trabajos pertenece a P0-2 y queda fuera de este cambio.

## Correcciones de esta reanudación

- Una respuesta HTTP que indica que Klipper dejó de estar `ready` invalida
  inmediatamente ese estado en `MachineState`, antes de propagar el error.
  Una orden posterior no puede reutilizar el `ready` anterior.
- Una observación HTTP con posición y sin velocidad no renueva el timestamp
  de velocidad. Una velocidad antigua no sirve como evidencia reciente de parada.
- El simulador del timeout de homing proporciona paquete Arduino reciente y
  observación de parada después de la emisión simulada.
- El simulador de pausa de malla de la API implementa el permiso compartido.
- Se añadieron regresiones para shutdown HTTP y respuestas sin velocidad.

## Validación

Comando desde la raíz de esta rama, con el entorno Python del proyecto:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests
git diff --check
```

La primera tanda enfocada ejecutó 198 pruebas: 197 correctas y un error del
simulador de homing. La primera suite completa ejecutó 376: 375 correctas y un
error del simulador de pausa de malla. Ambos simuladores se corrigieron.

Resultado final: **378 pruebas correctas en 63,988 segundos**; `git diff --check`
sin errores. No se ejecutaron checks del frontend porque esta reanudación solo
modificó backend, simuladores y documentación.

El cliente TestClient de la API quedó bloqueado dentro del sandbox, confirmado
con un volcado de hilos a los 20 segundos. La suite completa pudo ejecutarse
fuera del sandbox con autorización y conservando el modo simulado.

## Entrega

Commit local: `fix: centralize physical ownership and motion freshness`.
Sin push ni merge. P0-2 no se inicia en este cierre.

No se modificaron servicios, configuración operativa ni datos de producción.
Las pruebas automatizadas no constituyen validación física de la CNC.
