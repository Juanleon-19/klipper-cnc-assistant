# Cancelación, cierre y nueva preparación

Base: `991c48a789899eab3f12eb926cd8d1e6a4190d01`.
Rama: `fix/preparation-reset-lifecycle-20261026`.

## Causas confirmadas

1. `prepare_run` preserva correctamente el intento cancelado, pero `_is_stale_run`
   excluía los terminales y el CAS del archivado rechazaba los cancelados. No había
   cierre explícito desde la consola para comenzar otro intento con la misma malla.
2. Tras cancelar y recuperar la sesión, `_physical_leases` conservaba el handle
   anterior. El recorrido integral reprodujo `Lease físico inválido o de sesión
   anterior` al iniciar un trabajo nuevo después del reset completo.
3. Los formularios de receta no volvían a los valores iniciales. Lecturas iniciadas
   antes del reset podían reintroducir datos/errores después de completarlo.
4. El reset archivaba solo el mapa apuntado; los lectores compatibles podían
   encontrar otras versiones no archivadas. Además, declarar referencia confirmada
   sin referencia persistida era una presentación incorrecta.
5. Cada reintento del reset escribía otro histórico por fecha, aunque correspondiera
   a la misma revisión persistida.

## Correcciones y límites

- Cancelar conserva datos y referencias. Cerrar intento es otra acción explícita:
  archiva bajo reserva administrativa, evidencia remota, identidad y CAS; no reanuda
  ni envía un inicio. Después se crea una identidad nueva con operaciones pendientes.
- Los terminales son candidatos de cierre sin esperar un timeout artificial. Ser
  candidato no autoriza archivar: productor, recuperación, lock desconocido,
  impresión activa, velocidad inválida, identidad diferente e inicio ambiguo siguen
  bloqueando. Cancelación con archivo vacío sin evidencia suficiente sigue bloqueada.
- Tras retirar la asociación actual, se olvida únicamente el handle local retirado.
  No se libera ownership ni se adquiere un permiso sustituto durante el reset.
- Reset de mapa/referencia exige cerrar explícitamente un intento terminal asociado;
  el reset completo incluye el cierre. Mensaje con la acción siguiente antes de
  comenzar la transacción. No hay modificación silenciosa de la ejecución anterior.
- Reset archiva todas las versiones medidas aún vigentes del montaje y retira su
  asociación. Históricos conservados. Receta vuelve a los defaults existentes
  (7×6, retiros 2 mm, separación objetivo 10 mm, traslado relativo 10 mm, perfil
  de sonda heredado); no cambia configuración global ni algoritmo de sondeo.
- Homing no es requisito para archivar/invalidate sin movimiento. Se conserva
  Moonraker ready, impresión explícitamente inactiva, velocidad observada válida,
  reserva, sesión y CAS. Los guards de movimiento siguen exigiendo homing.
- Histórico administrativo determinista por identidad/revisión, comprobando sus
  campos críticos antes de reutilizarlo; nunca sobrescribe evidencia distinta.
- UI: «Recuperar preparación interrumpida», detalles técnicos plegados; «Cerrar
  intento y preparar otro»; progreso terminal identificado como historial, sin
  pedir referencia para la operación pendiente de ese intento. Respuestas y errores
  anteriores al reset se descartan por generación local; no alteran la autoridad backend.

No se modifican llegada observada, cambio físico de herramienta, JIT, sondeo,
firmware ni servicios productivos. Una máquina desconectada no acredita reposo.
La recuperación antigua sin evidencia de respaldo no se inventa ni se automatiza.

## Recorrido integral

React real → FastAPI real → servicios/runtime reales → transportes simulados,
con proyectos temporales y lock de proceso limitado al directorio temporal.

- D: fallo después de guardar histórico; recuperación explícita, reposo verificado,
  retry del reset sin duplicación del histórico.
- B: referencia y malla; primera operación completada de cinco; cancelación,
  recuperación, reset completo; `execution/live` sin ejecución; nueva referencia,
  nueva malla, nueva identidad, cinco pendientes y nueva evidencia JIT.
- A: cierre explícito; preparación/cancelación/cierre y otro intento con la misma
  malla conservada, sin iniciar automáticamente ni reutilizar permisos.
- C: reset de mapa sin mediciones activas; referencias conservadas. Regresión React
  verifica valores iniciales de filas, columnas y separación de la receta.
- E: recargas entre cambios de herramienta y después del reset; navegación
  responsive; no nuevas emisiones por recargar. Regresión de error HTTP tardío.
- Cinco operaciones completas del nuevo intento con confirmaciones humanas,
  perfiles estándar/largo y archivos JIT distintos; sin duplicar inicios.

## Validación final

- Frontend: 206/206 PASS (18 archivos).
- Focalizadas backend, guards, identidad, recuperación, ejecución, API y mapas:
  133/133 PASS.
- API E2E: 4/4 PASS dentro de la selección anterior.
- Browser E2E integral A–E: PASS; React y API reales, transportes falsos. Incluye
  seis inicios deliberados: uno del intento cancelado y cinco del intento nuevo.
- Lint, build y diff check: PASS. Advertencia preexistente de tamaño del bundle.
- Backend completo final: 857/857 PASS, 580,195 s.

Los fallos iniciales se conservaron como diagnóstico: cierre terminal rechazado y
permiso anterior retenido reproducidos antes de corregir. El harness de navegador
se adaptó a múltiples preparaciones, confirmaciones nativas y ausencia legítima de
recuperación tras cancelar sin envío. No se sustituyeron las respuestas del backend
por estados ficticios para aceptar esos pasos.

Comandos de validación: `python -m unittest discover -s tests -q`, selección de
`tests.test_administrative_remote_identity`, `tests.test_administrative_exclusion`,
`tests.test_administrative_recovery`, `tests.test_execution_state_machine`,
`tests.test_final_workflow_api`, `tests.test_physical_integration`;
`python tests/functional_journey_browser.py`; `npm run test -- --run`,
`npm run lint`, `npm run build`, `git diff --check`. Python siempre con
`MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=src:.`. Los datos y transportes de integración son temporales/falsos.

## Producción

No se consulta ni modifica el runtime físico. No se despliega ni se ejecutan resets
productivos. Revisión externa antes de instalar. No implica GO físico.

Nota de aislamiento: se reutilizaron dependencias Node ya instaladas mediante un
symlink temporal del worktree, retirado al finalizar. Vitest actualizó su caché
`node_modules/.vite/vitest/results.json` compartida; no se restauró ni se modificó
manualmente. No hubo instalación de paquetes, cambios de dependencias, build sobre
el frontend servido, ni escrituras a datos operativos. Futuras pruebas deben usar
caché aislada o deshabilitada para evitar ese efecto auxiliar.

Verificación final de 802 archivos operativos/protegidos: hashes sin cambios durante el cierre de validación. Main productivo permanece en la base; no se han ejecutado acciones físicas, resets productivos ni reinicios.

Resultado: CANDIDATA VALIDADA EN SIMULACIÓN — LISTA PARA REVISIÓN EXTERNA. No autoriza pruebas físicas ni despliegue.
