# Joystick y sondeo: respuesta, controles y recuperación

## Hallazgos y correcciones

- El jog consultaba HTTP en cada toque incluso con un frame autorizado fresco. Ahora reutiliza ese frame o refresca y reautoriza si falta evidencia. Se conservan ownership, homing, antigüedad serial, cancelación y límites; no hay cola de movimientos.
- La confirmación de cada paso esperaba inicialmente 250 ms para consultar. Ahora revisa primero la observación disponible y consulta inmediatamente si no confirma el movimiento. Las siguientes consultas siguen limitadas a intervalos de 250 ms. No se relajan tolerancias ni comprobaciones de contacto/reposo.
- Un contexto `ready` caducado durante sondeo se refresca antes de volver a autorizar el paso.
- Tras cancelar, la recuperación puede seguir bloqueada aunque el productor haya terminado. El API publica ese estado y Referencia ofrece **Verificar reposo y recuperar controles**. Reutiliza la reconciliación existente, exige observación HTTP de reposo y ausencia de productores/hijos/operaciones, y deja manual deshabilitado en diagnóstico. No envía movimiento ni reinicia conexiones o servicios.
- Las filas fraccionarias, vacías o inválidas y los retiros inválidos ya no se sustituyen silenciosamente para generar una vista previa.
- **Ver propuesta** aplica la cuadrícula y conserva su resumen, invalidando la vista previa anterior. Se elimina **Aceptar sugerencia**, que repetía los mismos valores. Se elimina de la interfaz una estimación que ignoraba la latencia por paso. El feed se etiqueta como velocidad programada, no como rendimiento total.

## Qué modifica cada control del mapa

| Control | Efecto |
| --- | --- |
| Filas / columnas | Cantidad y disposición de puntos |
| Separación objetivo | Densidad de la propuesta automática |
| Retiros por borde | Región local sondeada |
| Exclusiones | Puntos que no se ejecutan |
| Z segura de traslado | Separación sobre la referencia; afecta distancia de descenso |
| Heredar referencia | Paso, feed y retracto proceden del perfil de referencia |
| Override del mapa | Permite sustituir explícitamente paso, feed y retracto |
| Paso | Incremento de descenso; afecta cantidad de comprobaciones y resolución Z |
| Velocidad de descenso | Feed de cada paso, no duración total |
| Retracto | Separación posterior al contacto |

Cambiar parámetros requiere nueva vista previa y nuevo armado del mapa. Ningún valor físico persistido fue modificado. Por ejemplo, una separación de 10 mm con pasos de 0,05 mm puede requerir unos 200 pasos hasta la superficie; aumentar solo el feed no elimina las comprobaciones entre pasos. No se recomienda un valor físico sin conocer la holgura y la sonda.

El firmware fuente transmite cada 20 ms y usa umbrales cardinales amplios (250/750). No se ha cambiado ni cargado firmware; no se ha demostrado que coincida con el firmware instalado. La respuesta física final requiere validación posterior a integración.

## Validación aislada

Con `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false`, datos temporales y transportes falsos:

- Backend: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -q`: **583/583 PASS**.
- Incluye diez pruebas nuevas de respuesta/recuperación: frame fresco sin HTTP, refresco inmediato sin espera fija, contexto caducado, recuperación sin movimiento y rechazos por productor, hijo, velocidad o consulta fallida; contrato del API.
- Frontend completo: inicialmente 140/141; el fallo detectó borrado del resumen de propuesta. Corregido el orden de invalidación y repetido el archivo afectado: **65/65 PASS**. Los 141 casos distintos quedan aprobados; no se oculta el fallo inicial.
- Pruebas existentes de mapa verifican perfil heredado/override, feeds y parámetros enviados, validación de geometría, pausa, cancelación, reintentos y polling.

No se realizó prueba de movimiento físico ni se promete una latencia física específica. Esta rama no modifica `main` ni reinicia servicios.
- `npm run lint`: PASS. `npm run build`: PASS; conserva aviso existente de tamaño de bundle.
- Navegador Firefox con backend SIMULATED aislado, datos sintéticos y respuesta GET de estado físico simulada únicamente en navegador: controles de mapa, selector override y aviso de recuperación comprobados; capturas inspeccionadas a 1440×900 y 390×844, sin desbordamiento horizontal. Sin activar sondeo ni recuperación física.
- `git diff --check`: PASS. `graphify update .`: completado; artefactos locales no incluidos en Git.
