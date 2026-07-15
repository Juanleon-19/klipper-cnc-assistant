# Verificación servida - malla física y visor 2D

Fecha: 2026-07-14.

FastAPI sirve `frontend/dist` desde `http://127.0.0.1:8000/`.

HTML verificado por `curl`:

- `/assets/index-DuzJLtAD.js`
- `/assets/index-Cbpg_vAA.css`

Modo API verificado por `curl http://127.0.0.1:8000/api/machine/status`:

- `mode`: `PHYSICAL`
- `mode_label`: `FISICO`
- `application.mode`: `physical`

Captura visual: Firefox headless no generó PNG en este entorno. Primer intento informó perfil en uso; segundo intento con perfil temporal quedó bloqueado con `RenderCompositorSWGL failed mapping default framebuffer`. No se ejecutaron movimientos físicos.

Validación funcional cubierta por:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`
- `.venv/bin/python -m pip check`
- `npm --prefix frontend run lint`
- `npm --prefix frontend run test`
- `npm --prefix frontend run build`
