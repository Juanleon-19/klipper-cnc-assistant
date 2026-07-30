# Revisión de la Fase 1

Esta lista controla la revisión del pull request `fase-1/auditoria-arquitectura` antes de cualquier merge a `main`.

## Alcance

- [x] La auditoría identifica la copia realmente servida por systemd.
- [x] La matriz de procedencia distingue KEEP, RECOVER, REFACTOR, ARCHIVE y UNKNOWN.
- [ ] Confirmar mediante revisión final que la reorganización backend no cambia comportamiento deliberadamente.
- [ ] Confirmar mediante revisión final que la reorganización frontend conserva las funciones visibles.
- [x] README, PLAN, AGENTS y arquitectura describen la estructura actual de la rama.

## Configuración y despliegue

- [x] La configuración operativa dejó de estar versionada como fuente canónica.
- [x] `deploy/klipper-cnc-assistant.env.example` permanece en modo simulado.
- [x] El servicio versionado carga `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`.
- [ ] Antes del despliegue, copiar y verificar la configuración local existente en el servidor.
- [x] El servicio activo no se reinició como parte de la revisión del PR.

## Validación de software

- [x] CI backend segura aprobada con `MACHINE_MODE=simulated`.
- [x] CI frontend: lint aprobado.
- [x] CI frontend: pruebas aprobadas.
- [x] CI frontend: build aprobado.
- [x] Dependencias runtime importadas por Moonraker, Arduino y telemetría están declaradas.
- [x] La validación local previa documentó importación, backend temporal, `/api/health` y SPA.

## Seguridad física

- [x] No hubo G-code, homing, jog, probe, spindle, mapa físico ni ejecución.
- [x] No se modificó Klipper, Moonraker, Arduino ni systemd activo.
- [x] La Fase 2 no comenzó.

## Cierre

- [ ] Revisar la diferencia respecto a `main` por grupos funcionales.
- [ ] Resolver observaciones de código o documentación en la misma rama.
- [ ] Confirmar la migración de configuración antes de desplegar la rama fusionada.
- [ ] Aprobar explícitamente el merge commit a `main`.
