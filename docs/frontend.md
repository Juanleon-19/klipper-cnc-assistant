# Frontend

## Stack

- React 18
- TypeScript
- Vite 5
- CSS propio responsive
- Vitest + Testing Library
- ESLint

## Estructura principal

```text
frontend/
  src/
    components/
    lib/
    test/
    App.tsx
    main.tsx
    styles.css
```

## Vista funcional

La interfaz esta completamente en espanol e incluye:

- navegacion lateral;
- banner permanente `MÁQUINA EN MODO SIMULADO`;
- listado real de proyectos;
- formulario de creacion y edicion;
- seleccion de operaciones por PCB;
- carga de G-code por archivo real;
- analisis mostrado por operacion;
- vista previa 2D SVG con zoom, desplazamiento y restablecer;
- diagnostico del sistema.

## Desarrollo local

```bash
cd frontend
npm install
npm run dev
```

Durante desarrollo, Vite usa proxy a `http://127.0.0.1:8000/api`.

## Validacion

```bash
cd frontend
npm run lint
npm run test
npm run build
```

## Limitaciones actuales

- no hay frontend 3D;
- no hay Gerber;
- no hay mapa de alturas;
- G2/G3 se advierten pero no se renderizan geometricamente;
- la vista previa no es simulacion de mecanizado.
