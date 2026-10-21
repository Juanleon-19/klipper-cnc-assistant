# Identidad remota durante administración — corrección focalizada

Base: `cece9559e8d212ce489f7cb4ec07bf8b998137d5`.
Rama: `fix/administrative-remote-identity-20261021`.
Worktree aislado: `administrative-remote-identity-20261021`.
Sin push, merge, despliegue ni reinicios. Sin comandos físicos.

## Causa y evidencia

El journal productivo registra dos POST reset-preparation rechazados con HTTP 400
el 21 de octubre a las 11:41:21 y 11:41:35 (hora Bogotá). Corresponden a la acción
«Reiniciar proceso». Hay accesos a esa misma acción en ProjectWorkspace y App;
el access log no permite identificar cuál de los botones se pulsó.

La lectura directa del JobRun confirma SPINDLE_STOP_REQUIRED, revisión 11,
primera operación COMPLETED y start_attempt sent. El archivo esperado coincide
con moonraker_filename de esa operación y con remote_file del journal de inicio.
Existen observed_printing=true y finished_at. No se incluyen aquí identificadores
ni G-code productivos.

La consulta HTTP GET de objetos Moonraker, realizada solo para diagnóstico,
respondió ready, standby, filename="", virtual_sdcard.is_active=false,
homed_axes=xyz y live_velocity=0.0. El snapshot local respondió DIAGNOSTIC,
ownership IDLE, producer_active=false, dispatching=0, children=0,
recovery_pending=false y active_operation=null. Estas son observaciones actuales,
no una reconstrucción garantizada de cada respuesta de los POST anteriores.
No se conectó/reconectó el runtime ni se ejecutó una acción de recuperación.

El defecto de clasificación sí es determinista: PrintIdentity.require_match
rechaza un filename vacío como identidad inválida; el catch anterior convierte
cualquier PrintIdentityError en «Identidad remota diferente». Un standby vacío
explícito no prueba otro archivo ni demuestra por sí solo finalización.
Una regresión ejecutada con el método extraído de cece955 reproduce exactamente
el error anterior. Se parcheó solo la clase del proceso de prueba, no producción.

## Cambio mínimo

Solo cambia JobService._assert_administrative_print_identity. Mantiene
PrintIdentity sin modificaciones y separa:

- inicio ambiguo: bloqueado antes de comparar nombres;
- conexión/ready no verificables: mensaje específico, conserva ejecución;
- nombre ausente/malformado: no verificable, conserva ejecución;
- nombre no vacío diferente: discrepancia real, conserva ejecución;
- nombre coincidente: flujo administrativo existente;
- nombre explícitamente vacío: excepción estrecha descrita a continuación.

Solo se acepta standby vacío en SPINDLE_STOP_REQUIRED o JOB_COMPLETE, con
is_active_observed=false, operación actual COMPLETED, observed_printing=true,
finished_at, moonraker_filename igual a la identidad esperada y start_attempt
sent/may_have_been_sent=true ligado exactamente a esa operación y archivo.
No se cambia el token, no se inventa filename y no se interpreta None como vacío.

Antes de llegar aquí siguen siendo obligatorios observación HTTP real, Klipper
ready, homing XYZ, velocidad válida dentro de tolerancia, ownership libre,
MovementLock observable/libre, ausencia de workers/recovery y reserva
administrativa. Se añade guard.check antes de usar la evidencia; permanecen
frescura, sesión, CAS, cancelación y journal de fallo parcial existentes.
No se altera la política de movimiento ni la máquina de estados.

## Flujo seguro disponible tras revisión y despliegue autorizados

1. Mantener el trabajo antiguo sin cambios hasta autorizar expresamente una acción
   administrativa. No editar current_run ni eliminar locks.
2. Si ownership/recovery o evidencia remota no cumplen los guards, conservar el
   bloqueo y resolverlos mediante su procedimiento legítimo. Este parche no los
   libera y no requiere ejecutar un archivo para fabricar identidad.
3. Para conservar mapa/referencias y el avance histórico, usar el archivado
   explícito existente de ejecución obsoleta cuando cumpla sus criterios. Guarda
   histórico antes de retirar el registro actual mediante CAS. No ejecutarlo en
   producción como parte de esta corrección.
4. «Reiniciar proceso» también puede pasar en el caso demostrado, pero conserva su
   alcance destructivo lógico: archiva ejecución/mapa e invalida preparación y
   referencias dependientes. No equivale al archivado simple.
5. Preparar pruebas nuevas explícitamente. No hay start automático ni reanudación
   del trabajo anterior. Una ejecución nueva no hereda automáticamente el avance:
   seleccionar/revisar sus operaciones para evitar volver a mecanizar la primera.

## Regresiones y validación

11 tests nuevos con almacenamiento temporal y transportes fake:
archivado/reset con standby vacío completado; discrepancia no vacía; nombre
None/malformado; vacío fuera de standby; evidencia local insuficiente; intentos
ambiguos; binding del intento; shutdown/desconexión/homing/velocidad/actividad;
ownership/recovery; coincidencia tradicional; archivado preservando dependencias
y preparación explícita de nuevo run sin start ni probe.
Los rechazos verifican bytes intactos y ausencia de start/cancel fake.
Las 19 regresiones administrativas existentes y las pruebas de concurrencia/CAS
se conservan sin cambios.

- Regresión sobre implementación base: fallo original reproducido (esperado).
- Regresiones nuevas: 11/11 PASS.
- Focalizados (nuevos + exclusión administrativa + administración offline + API): 45/45 PASS.
- API E2E simulados: 4/4 PASS, incluidos en los focalizados.
- Backend completo: 811/811 PASS (360.383 s).
- Frontend/lint/build: NOT_RUN; no se modificó frontend ni contrato HTTP.
- Diff check: PASS.
- JobRun productivo: hash idéntico al baseline privado previo al despliegue.

Comandos (todos con MACHINE_MODE=simulated, MACHINE_AUTO_CONNECT=false,
PYTHONDONTWRITEBYTECODE=1, PYTHONPATH=src:., PYTHONWARNINGS=ignore):

```bash
python -m unittest tests.test_administrative_remote_identity -q
python -m unittest tests.test_administrative_remote_identity tests.test_administrative_exclusion tests.test_offline_administration tests.test_final_workflow_api -q
python -m unittest discover -s tests -q
git diff --check
```

## Archivos

- execution/job_service.py: clasificación, mensajes accionables y excepción
  administrativa limitada a evidencia de operación terminada.
- tests/test_administrative_remote_identity.py: 11 regresiones simuladas.
- Este informe: evidencia, alcance, límites y procedimiento de aplicación.

## Aplicación posterior

Auditoría externa del diff y pruebas; integrar únicamente tras autorización.
Desplegar siguiendo el procedimiento de mantenimiento ya existente, con respaldo,
exclusión y autorización separada de parada/arranque. Ninguna instalación de
paquetes es necesaria. Después comprobar diagnóstico y preservación del JobRun
antes de autorizar cualquier archivado/reset. No aplicar cambios de datos a mano.

PRODUCTION_CODE_MODIFIED = NO
PRODUCTIVE_JOB_MODIFIED_BY_THIS_TASK = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
PHYSICAL_VALIDATION_PENDING = YES

SOFTWARE_VALIDATED = YES
READY_FOR_EXTERNAL_AUDIT = YES
El HEAD final se obtiene con `git rev-parse HEAD` en esta rama; no se publica.

## Revisión focalizada previa a integración

Revisado el diff completo contra cece955: no se encontró debilitamiento de
ownership, guard administrativo, homing/velocidad, frescura, CAS o journal.
Repetición de 45 tests focalizados (incluidos 11 nuevos y 4 API E2E): PASS.
Se reutiliza el resultado reciente 811/811 del mismo código backend.
Corregida únicamente una duplicación editorial de este informe; sin cambio de
implementación. Integración/despliegue posteriores autorizados por el operador,
condicionados a los gates de mantenimiento. No implica autorización de reset
sobre el trabajo productivo ni aprobación de pruebas físicas.
