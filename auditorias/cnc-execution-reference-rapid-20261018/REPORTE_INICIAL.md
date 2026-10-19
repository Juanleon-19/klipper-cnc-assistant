# Auditoría inicial (sin movimiento)

Base b0dbcdc691a145b4b086dcd9cc2dcf1795e10880; main y origin/main locales coinciden.
MotionContext/start_attempt de 6dec436 son ancestros. La rama histórica de integración runtime no es ancestro y no se incorpora. El hotfix histórico af0099d sí forma parte del historial existente; no se integra trabajo adicional.

P0: consulta GET a Moonraker confirma motion_report presente, XYZ finita y live_velocity=-5.551115123125783e-17. El código local de Klipper calcula velocidad como start_v + accel * move_time sin normalizar cancelación numérica. _refresh_machine rechaza todo negativo. El journal registra tres HTTP 400 tras terminar la operación. Actualmente Klipper está shutdown y sin homing: sigue siendo bloqueo legítimo independiente. No se intenta recuperar.

JobRun leído: SPINDLE_STOP_REQUIRED, revisión 11, primera operación COMPLETED y cuatro pendientes. Copias privadas y SHA256 antes del trabajo en /tmp/kca-cnc-audit-20261018; no se incorporan datos productivos a Git. No se consulta execution/live porque puede reconciliar persistencia.

P1 velocidades: original FlatCAM tiene F25 modal y rapids 1500 solamente en comentario. Legacy convierte todo movimiento a G1 F25. Además Klipper registra G0 con el MISMO handler cmd_G1: cambiar solo la letra no acelera. No se debe inferir un feed autorizado de un comentario. Se analizarán distancia, subdivisión y seguridad antes de cualquier optimización.

P1 perfiles: el endpoint de prueba de estación no transmite perfil; usa standard. La aproximación tiene un único valor; el antiguo long_tool_reference_prep_z_mm es alias de clearance, NO debe reinterpretarse como aproximación. Separar homing/preparación requiere preservar API combinada y guardias.

Riesgo geométrico: no existe modelo de abrazaderas/longitud física completo. Alturas configuradas requieren validación física explícita; ninguna prueba fake aprueba colisiones físicas.
