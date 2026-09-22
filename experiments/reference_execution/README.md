# Benchmark aislado de referencia y ejecución

`benchmark.py` copia un proyecto a un directorio temporal, instancia servicios en
modo simulado sin arrancar conexiones y mide preview, lectura de plan, prepare,
prepare repetido y armado de un mapa nuevo. No ejecuta trabajos ni sondas.

Desde la raíz del repositorio:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:. \
  python experiments/reference_execution/benchmark.py \
  --source /ruta/a/copia-privada-del-proyecto --output /tmp/benchmark.json
```

Usar la misma copia congelada y los mismos márgenes para comparar revisiones.
Los márgenes por defecto reproducen el escenario de la revisión; son opciones
`--right-margin` y `--top-margin`. El origen se lee, nunca se modifica. No
versionar la copia, mapas, tokens, G-code ni identificadores reales.

Los tiempos internos son inclusivos: no se suman entre categorías anidadas.
Un contador cero de análisis significa que se reutilizó el análisis persistido;
no demuestra que su deserialización fuera gratuita. Una copia sin `generated/`
no mide el coste de verificar un historial de artefactos. La preparación incluye
un runtime de prueba listo: una referencia numérica histórica sin token sigue
bloqueada, aunque se mida el tiempo que tarda en mostrar ese resultado.

`--cold-prepare` omite la lectura previa del plan para medir también la primera
preparación sin caché. Registrar ambos escenarios, sin presentar el tiempo de
reutilización como si incluyera la primera construcción.
