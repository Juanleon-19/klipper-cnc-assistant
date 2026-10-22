# Contrato funcional y continuidad

Leer este documento antes de cambiar referencias, malla, ejecución o administración.
El objetivo es preparar y ejecutar operaciones de un montaje con una sola malla,
referencias válidas por instalación y confirmaciones humanas explícitas. No se
considera terminado un cambio por compilar o pasar pruebas aisladas.

## Responsabilidades

React presenta acciones y errores; FastAPI valida solicitudes; application coordina
proyectos/referencia/mapa; execution coordina JobRun y JIT; MachineRuntime y
MotionAuthorizer autorizan la frontera física; storage conserva revisiones e historial.
La interfaz nunca es una autoridad de movimiento.

## Recorrido

Abrir proyecto/montaje → reinicio explícito y seguro, si corresponde → conectar →
homing XYZ → preparar altura y centro → joystick XY → guardar conjuntamente XY
actual y referencia Z → preview → validar cobertura → armar → medir malla → preparar
JobRun → confirmar spindle → Legacy JIT por operación → upload → start autorizado.
Misma herramienta: reutilizar instalación vigente y generar JIT nuevo. Cambio:
SPINDLE_STOP_REQUIRED → confirmación → despeje saliente → TOOL_CHANGE_REQUIRED →
instalación entrante → retorno al XY del mapa → una Z → READY_TO_RESUME → confirmación
→ JIT. No remallar ni rehabilitar tokens históricos.

## Persistencia y barreras

JobRun conserva revisión CAS, start_attempt e historial; cancelar domina sobre
escrituras tardías. Sending/uncertain no autorizan repetición automática.
Referencias ligan sesión, instalación, contexto y configuración. El mapa liga
montaje/cara/superficie/placement; nunca pertenece exclusivamente a una herramienta.
Ownership, MovementLock observable, ausencia de productores y recovery resuelta
son condiciones independientes de que un hilo parezca inactivo.

La observación física no es un timeout para escrituras administrativas. Consultar
estado remoto real en fronteras de mutación, bajo reserva administrativa. Timestamp
al recibir la observación; comprobar identidad y sesión. La reserva impide nuevos
productores locales y no autoriza movimiento. CAS protege los registros. Un error
parcial conserva current_run e histórico y deja administrative-reset.pending.json;
no reejecutar ni borrar ese journal automáticamente. No hay transacción distribuida
con Moonraker ni rollback automático de dependencias ya escritas.

## Regresiones obligatorias

- HTTP y disco lentos; cambio remoto entre observaciones; fallo de escritura.
- Archivo vacío standby con evidencia completa frente a identidad diferente,
  desconocida, inicio ambiguo, movimiento, desconexión o cambio de sesión.
- Ownership retenido, recovery, lock desconocido, CAS y cancelación concurrente.
- Captura XY/Z → malla → JIT → varias operaciones/cambio de herramienta.
- Recarga del navegador y persistencia; confirmaciones humanas y errores visibles.
- Muestras XYZ iguales siguen frescas; actividad WS sin posición no autoriza.

## Estado verificable

Base validada anterior: `83ebcde64e946e61d08d178e537dd17c0db97b67`.
Candidata en desarrollo: `fix/functional-stabilization-20261021`.
Recorrido integral de navegador validado con transportes simulados y tres
operaciones A → A → B. Resultados completos en
`auditorias/functional-stabilization-20261021/REPORTE_FINAL.md`. No declarar validación física mediante simulación.
Producción, datos, servicios y hardware quedan fuera del desarrollo.
