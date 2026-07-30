# Revisión de la Fase 1

Esta lista controla la revisión del pull request `fase-1/auditoria-arquitectura` antes de cualquier merge a `main`.

## Alcance

- [ ] La auditoría identifica la copia realmente servida por systemd.
- [ ] La matriz de procedencia distingue KEEP, RECOVER, REFACTOR, ARCHIVE y UNKNOWN.
- [ ] La reorganización backend no cambia comportamiento deliberadamente.
- [ ] La reorganización frontend conserva las funciones visibles.
- [ ] README, PLAN, AGENTS y arquitectura describen el código real.

## Configuración y despliegue

- [ ] La configuración operativa no está versionada.
- [ ] `deploy/klipper-cnc-assistant.env.example` permanece en modo simulado.
- [ ] El servicio versionado carga `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`.
- [ ] Antes del despliegue se copia y verifica la configuración local existente.
- [ ] El servicio activo no se reinicia como parte de la revisión del PR.

## Validación de software

- [ ] Backend seguro: 94/94 pruebas aprobadas con `MACHINE_MODE=simulated`.
- [ ] Frontend: lint aprobado.
- [ ] Frontend: 63/63 pruebas aprobadas.
- [ ] Frontend: build aprobado.
- [ ] Importación del paquete aprobada.
- [ ] Backend temporal y `/api/health` aprobados sin hardware.

## Seguridad física

- [ ] No hubo G-code, homing, jog, probe, spindle, mapa físico ni ejecución.
- [ ] No se modificó Klipper, Moonraker, Arduino ni systemd activo.
- [ ] No se autoriza la Fase 2 hasta cerrar este PR.

## Cierre

- [ ] Revisar los 86 commits respecto a `main` por grupos funcionales.
- [ ] Resolver observaciones del PR en la misma rama.
- [ ] Confirmar migración de la configuración antes de desplegar la rama fusionada.
- [ ] Aprobar explícitamente el merge commit a `main`.
