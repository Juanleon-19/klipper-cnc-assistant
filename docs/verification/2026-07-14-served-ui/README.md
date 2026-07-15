# Verificación servida 2026-07-14

FastAPI sirve `frontend/dist` y el HTML verificado por `curl` referencia:

- `/assets/index-DNVlB1UT.js`
- `/assets/index-yhqof53C.css`

El endpoint real `GET /api/projects/proj_5dcb4ee1db/operations/op_bb1c8827b7/reference-session` devuelve `origen_trabajo.posicion_captura` como objeto `CapturedPosition` y ya no produce error Pydantic.

Intento de capturas: `firefox --headless --screenshot` quedó bloqueado en este entorno con `RenderCompositorSWGL failed mapping default framebuffer` y no generó PNG. No se instaló software ni se ejecutaron movimientos físicos. La verificación visual funcional queda cubierta por `frontend/src/components/ProjectWorkspace.test.tsx` y `frontend/src/features/heightmap/HeightMapViews.test.tsx`, ejecutadas contra los componentes reales y por el HTML servido con el build final.
