# Retraso de joystick y caducidad al guardar referencia

## Problemas reproducidos

El callback serial esperaba HTTP, envio y confirmacion del jog antes de leer el siguiente paquete Arduino. Esto permitia acumular entradas durante el movimiento. Ademas, la pantalla realizaba cuatro peticiones sucesivas para sondear, refrescar, capturar origen y guardar Z. La ultima podia llegar despues del limite de validez de la medicion, aunque el contacto se hubiera detectado correctamente.

## Correccion

- Worker unico sin cola para jog; adquiere ownership antes de arrancar. Los paquetes y estados de sonda siguen procesandose durante la espera. Los toques recibidos durante un jog no se ejecutan despues. Se necesita un CENTER nuevo al terminar.
- Se conserva el perfil del toque inicial y se rechazan intenciones caducadas. No cambian feeds, distancias, homing requerido ni las validaciones previas a emitir.
- Endpoint `reference-session/probe-and-capture`: sondeo y guardado dentro de una peticion, conservando ownership hasta persistir. El origen X/Y y el Z de contacto se guardan juntos, previa validacion del contexto. El frontend refresca despues de guardar.
- Se conserva el rechazo de sondas historicas obsoletas. No se sustituye el contacto por la posicion retraida ni se rejuvenecen timestamps.

## Validacion

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest \
  tests.test_joystick_worker tests.test_probe_and_capture \
  tests.test_joystick_fresh_observation tests.test_machine_runtime tests.test_api \
  tests.test_final_stabilization tests.test_motion_authorization tests.test_physical_ownership \
  tests.test_runtime_serial_lifecycle tests.test_serial_driver_lifecycle tests.test_serial_physical_policy -q
cd frontend
npm test -- src/features/projects/ProjectWorkspace.test.tsx
npm run lint
npm run build
```

Resultado: 197/197 pruebas backend y 64/64 de la pantalla de proyectos. Las pruebas usan directorios temporales y dobles de transportes/permisos. TestClient se bloqueo dentro del aislamiento en la comunicacion interna de AnyIO; se repitio fuera del aislamiento con las mismas variables simuladas, sin servicios reales. Lint, build y `git diff --check` pasan; permanece el aviso previo de tamanio de bundle.

Las regresiones incluyen 200 paquetes recibidos durante un jog sin producir una cola, cancelacion, desconexion, cambio de generacion Arduino, toque caducado con paquetes recientes, cambio de modo durante consulta y fallo de arranque del worker. Para referencia: reproduce el error tras 22 segundos del flujo anterior; valida el guardado del contacto Z=30 con posicion retraida Z=31, respuesta tardia, fallo de sondeo/disco, ownership ocupado, contexto cambiado, sonda caducada y recuperacion ante emision incierta.

No se han emitido movimientos ni reiniciado servicios reales para validar. Sigue existiendo la latencia de la observacion HTTP necesaria antes del jog; se elimina la acumulacion serial, no esa comprobacion. La respuesta fisica y el sondeo quedan pendientes de validacion por el operador tras instalar.
