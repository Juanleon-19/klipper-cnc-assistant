from __future__ import annotations

from dataclasses import replace

from klipper_cnc_assistant.domain import ProjectValidationError
from klipper_cnc_assistant.heightmap import (
    HeightGrid,
    HeightMap,
    HeightSample,
    SampleQuality,
    build_dense_surface,
    compute_height_map,
    generate_simulated_height_map,
    parse_csv_samples,
    parse_json_samples,
)
from klipper_cnc_assistant.storage import JsonProjectRepository

from .errors import ApplicationError, NotFoundError


_UNSET = object()


class HeightMapService:
    def __init__(self, repository: JsonProjectRepository) -> None:
        self.repository = repository

    def configure_map(
        self,
        *,
        project_id: str,
        operation_id: str,
        filas: int,
        columnas: int,
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        grid = self._grid_from_project(project.material.ancho_mm, project.material.alto_mm, filas, columnas)
        samples = self._blank_samples(grid)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=self._next_version(project_id, operation_id),
            fuente_datos="manual",
            escenario=None,
            semilla=None,
            etiqueta_simulada=False,
            grid=grid,
            muestras=samples,
            estado="sin datos",
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(height_map))
        return height_map

    def generate_simulated_map(
        self,
        *,
        project_id: str,
        operation_id: str,
        filas: int,
        columnas: int,
        escenario: str,
        semilla: int,
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        height_map = generate_simulated_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=self._next_version(project_id, operation_id),
            ancho_mm=project.material.ancho_mm,
            alto_mm=project.material.alto_mm,
            filas=filas,
            columnas=columnas,
            escenario=escenario,
            semilla=semilla,
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(height_map))
        return height_map

    def import_json_map(
        self,
        *,
        project_id: str,
        operation_id: str,
        content: str,
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        grid, samples = parse_json_samples(content)
        normalized_grid = self._normalize_grid(project.material.ancho_mm, project.material.alto_mm, grid, samples)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=self._next_version(project_id, operation_id),
            fuente_datos="json",
            escenario=None,
            semilla=None,
            etiqueta_simulada=False,
            grid=normalized_grid,
            muestras=samples,
            estado="importado",
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(height_map))
        return height_map

    def import_csv_map(
        self,
        *,
        project_id: str,
        operation_id: str,
        content: str,
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        grid, samples = parse_csv_samples(content)
        normalized_grid = self._normalize_grid(project.material.ancho_mm, project.material.alto_mm, grid, samples)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=self._next_version(project_id, operation_id),
            fuente_datos="csv",
            escenario=None,
            semilla=None,
            etiqueta_simulada=False,
            grid=normalized_grid,
            muestras=samples,
            estado="importado",
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(height_map))
        return height_map

    def get_map(
        self,
        project_id: str,
        operation_id: str,
    ) -> HeightMap:
        self._load_project(project_id).get_operation(operation_id)
        try:
            payload = self.repository.load_height_map_payload(project_id, operation_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        return self._deserialize_map(payload)

    def get_statistics(
        self,
        project_id: str,
        operation_id: str,
    ):
        return self.get_map(project_id, operation_id).estadisticas

    def update_sample(
        self,
        *,
        project_id: str,
        operation_id: str,
        sample_id: str,
        z_mm: float | None | object = _UNSET,
        incluida: bool | object = _UNSET,
        observacion: str | None | object = _UNSET,
    ) -> HeightMap:
        current = self.get_map(project_id, operation_id)
        updated_samples: list[HeightSample] = []
        found = False
        for sample in current.muestras:
            if sample.id != sample_id:
                updated_samples.append(sample)
                continue
            found = True
            next_sample = sample
            if z_mm is not _UNSET:
                next_sample = replace(
                    next_sample,
                    z_mm=z_mm,
                    estado_calidad=SampleQuality.FALTANTE if z_mm is None else SampleQuality.VALIDA,
                )
            if incluida is not _UNSET:
                next_sample = replace(
                    next_sample,
                    incluida=bool(incluida),
                    estado_calidad=SampleQuality.EXCLUIDA if not bool(incluida) else next_sample.estado_calidad,
                )
            if observacion is not _UNSET:
                next_sample = replace(next_sample, observacion=observacion)
            updated_samples.append(next_sample)
        if not found:
            raise NotFoundError(f"La muestra '{sample_id}' no existe.")
        recalculated = compute_height_map(
            proyecto_id=current.proyecto_id,
            operacion_id=current.operacion_id,
            version=current.version + 1,
            fuente_datos=current.fuente_datos,
            escenario=current.escenario,
            semilla=current.semilla,
            etiqueta_simulada=current.etiqueta_simulada,
            grid=current.grid,
            muestras=updated_samples,
            estado=current.estado,
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(recalculated))
        return recalculated

    def recalculate_map(
        self,
        project_id: str,
        operation_id: str,
    ) -> HeightMap:
        current = self.get_map(project_id, operation_id)
        recalculated = compute_height_map(
            proyecto_id=current.proyecto_id,
            operacion_id=current.operacion_id,
            version=current.version + 1,
            fuente_datos=current.fuente_datos,
            escenario=current.escenario,
            semilla=current.semilla,
            etiqueta_simulada=current.etiqueta_simulada,
            grid=current.grid,
            muestras=list(current.muestras),
            estado=current.estado,
        )
        self.repository.save_height_map_payload(project_id, operation_id, self._serialize_map(recalculated))
        return recalculated

    def delete_map(self, project_id: str, operation_id: str) -> None:
        self._load_project(project_id).get_operation(operation_id)
        try:
            self.repository.delete_height_map(project_id, operation_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error

    def build_surfaces(self, height_map: HeightMap) -> dict[str, dict[str, object]]:
        return {
            "bruto": build_dense_surface(height_map, mode="bruto"),
            "plano": build_dense_surface(height_map, mode="plano"),
            "residuo": build_dense_surface(height_map, mode="residuo"),
        }

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error

    def _next_version(self, project_id: str, operation_id: str) -> int:
        try:
            current = self.repository.load_height_map_payload(project_id, operation_id)
        except FileNotFoundError:
            return 1
        return int(current.get("version", 0)) + 1

    def _grid_from_project(self, ancho_mm: float, alto_mm: float, filas: int, columnas: int) -> HeightGrid:
        if filas < 2 or columnas < 2:
            raise ApplicationError("La malla del mapa requiere al menos 2 filas y 2 columnas.")
        return HeightGrid(
            filas=filas,
            columnas=columnas,
            ancho_mm=ancho_mm,
            alto_mm=alto_mm,
            paso_x_mm=ancho_mm / (columnas - 1),
            paso_y_mm=alto_mm / (filas - 1),
        )

    def _blank_samples(self, grid: HeightGrid) -> list[HeightSample]:
        samples: list[HeightSample] = []
        for fila in range(grid.filas):
            for columna in range(grid.columnas):
                samples.append(
                    HeightSample(
                        id=f"hm_{fila}_{columna}",
                        x_mm=columna * grid.paso_x_mm,
                        y_mm=fila * grid.paso_y_mm,
                        z_mm=None,
                        fila=fila,
                        columna=columna,
                        origen_datos="manual",
                        estado_calidad=SampleQuality.FALTANTE,
                        observacion="Pendiente de datos.",
                    )
                )
        return samples

    def _normalize_grid(
        self,
        ancho_material_mm: float,
        alto_material_mm: float,
        grid: HeightGrid,
        samples: list[HeightSample],
    ) -> HeightGrid:
        rows = grid.filas or max(sample.fila for sample in samples) + 1
        columns = grid.columnas or max(sample.columna for sample in samples) + 1
        if rows < 2 or columns < 2:
            raise ApplicationError("El mapa importado requiere al menos 2 filas y 2 columnas.")
        width = grid.ancho_mm or ancho_material_mm
        height = grid.alto_mm or alto_material_mm
        step_x = grid.paso_x_mm or (width / (columns - 1))
        step_y = grid.paso_y_mm or (height / (rows - 1))
        return HeightGrid(
            filas=rows,
            columnas=columns,
            ancho_mm=width,
            alto_mm=height,
            paso_x_mm=step_x,
            paso_y_mm=step_y,
        )

    def _serialize_map(self, height_map: HeightMap) -> dict[str, object]:
        return {
            "proyecto_id": height_map.proyecto_id,
            "operacion_id": height_map.operacion_id,
            "version": height_map.version,
            "version_algoritmo": height_map.version_algoritmo,
            "estado": height_map.estado,
            "fuente_datos": height_map.fuente_datos,
            "escenario": height_map.escenario,
            "semilla": height_map.semilla,
            "etiqueta_simulada": height_map.etiqueta_simulada,
            "grid": {
                "filas": height_map.grid.filas,
                "columnas": height_map.grid.columnas,
                "ancho_mm": height_map.grid.ancho_mm,
                "alto_mm": height_map.grid.alto_mm,
                "paso_x_mm": height_map.grid.paso_x_mm,
                "paso_y_mm": height_map.grid.paso_y_mm,
            },
            "muestras": [
                {
                    "id": sample.id,
                    "x_mm": sample.x_mm,
                    "y_mm": sample.y_mm,
                    "z_mm": sample.z_mm,
                    "fila": sample.fila,
                    "columna": sample.columna,
                    "origen_datos": sample.origen_datos,
                    "estado_calidad": sample.estado_calidad,
                    "observacion": sample.observacion,
                    "incluida": sample.incluida,
                    "residuo_plano_mm": sample.residuo_plano_mm,
                }
                for sample in height_map.muestras
            ],
            "estadisticas": {
                "cantidad_puntos": height_map.estadisticas.cantidad_puntos,
                "cantidad_puntos_incluidos": height_map.estadisticas.cantidad_puntos_incluidos,
                "cantidad_puntos_faltantes": height_map.estadisticas.cantidad_puntos_faltantes,
                "cantidad_puntos_atipicos": height_map.estadisticas.cantidad_puntos_atipicos,
                "altura_min_mm": height_map.estadisticas.altura_min_mm,
                "altura_max_mm": height_map.estadisticas.altura_max_mm,
                "rango_alturas_mm": height_map.estadisticas.rango_alturas_mm,
                "rms_residuos_mm": height_map.estadisticas.rms_residuos_mm,
                "residuo_maximo_mm": height_map.estadisticas.residuo_maximo_mm,
                "ancho_cubierto_mm": height_map.estadisticas.ancho_cubierto_mm,
                "alto_cubierto_mm": height_map.estadisticas.alto_cubierto_mm,
            },
            "plano": None if height_map.plano is None else {
                "a": height_map.plano.a,
                "b": height_map.plano.b,
                "c": height_map.plano.c,
                "inclinacion_x_mm_por_mm": height_map.plano.inclinacion_x_mm_por_mm,
                "inclinacion_y_mm_por_mm": height_map.plano.inclinacion_y_mm_por_mm,
                "rms_residuos_mm": height_map.plano.rms_residuos_mm,
                "residuo_maximo_mm": height_map.plano.residuo_maximo_mm,
                "residuo_minimo_mm": height_map.plano.residuo_minimo_mm,
            },
            "creado_en": height_map.creado_en.isoformat(),
            "actualizado_en": height_map.actualizado_en.isoformat(),
        }

    def _deserialize_map(self, payload: dict[str, object]) -> HeightMap:
        from datetime import datetime
        from klipper_cnc_assistant.heightmap import HeightMapStatistics, PlaneFit

        grid_payload = payload["grid"]
        stats_payload = payload["estadisticas"]
        plane_payload = payload.get("plano")
        return HeightMap(
            proyecto_id=str(payload["proyecto_id"]),
            operacion_id=str(payload["operacion_id"]),
            version=int(payload["version"]),
            version_algoritmo=str(payload["version_algoritmo"]),
            estado=str(payload["estado"]),
            fuente_datos=str(payload["fuente_datos"]),
            escenario=str(payload["escenario"]) if payload.get("escenario") is not None else None,
            semilla=int(payload["semilla"]) if payload.get("semilla") is not None else None,
            etiqueta_simulada=bool(payload.get("etiqueta_simulada", False)),
            grid=HeightGrid(
                filas=int(grid_payload["filas"]),
                columnas=int(grid_payload["columnas"]),
                ancho_mm=float(grid_payload["ancho_mm"]),
                alto_mm=float(grid_payload["alto_mm"]),
                paso_x_mm=float(grid_payload["paso_x_mm"]),
                paso_y_mm=float(grid_payload["paso_y_mm"]),
            ),
            muestras=tuple(
                HeightSample(
                    id=str(sample["id"]),
                    x_mm=float(sample["x_mm"]),
                    y_mm=float(sample["y_mm"]),
                    z_mm=None if sample.get("z_mm") is None else float(sample["z_mm"]),
                    fila=int(sample["fila"]),
                    columna=int(sample["columna"]),
                    origen_datos=str(sample["origen_datos"]),
                    estado_calidad=SampleQuality(str(sample["estado_calidad"])),
                    observacion=str(sample["observacion"]) if sample.get("observacion") is not None else None,
                    incluida=bool(sample.get("incluida", True)),
                    residuo_plano_mm=None if sample.get("residuo_plano_mm") is None else float(sample["residuo_plano_mm"]),
                )
                for sample in payload.get("muestras", [])
            ),
            estadisticas=HeightMapStatistics(
                cantidad_puntos=int(stats_payload["cantidad_puntos"]),
                cantidad_puntos_incluidos=int(stats_payload["cantidad_puntos_incluidos"]),
                cantidad_puntos_faltantes=int(stats_payload["cantidad_puntos_faltantes"]),
                cantidad_puntos_atipicos=int(stats_payload["cantidad_puntos_atipicos"]),
                altura_min_mm=None if stats_payload.get("altura_min_mm") is None else float(stats_payload["altura_min_mm"]),
                altura_max_mm=None if stats_payload.get("altura_max_mm") is None else float(stats_payload["altura_max_mm"]),
                rango_alturas_mm=None if stats_payload.get("rango_alturas_mm") is None else float(stats_payload["rango_alturas_mm"]),
                rms_residuos_mm=None if stats_payload.get("rms_residuos_mm") is None else float(stats_payload["rms_residuos_mm"]),
                residuo_maximo_mm=None if stats_payload.get("residuo_maximo_mm") is None else float(stats_payload["residuo_maximo_mm"]),
                ancho_cubierto_mm=None if stats_payload.get("ancho_cubierto_mm") is None else float(stats_payload["ancho_cubierto_mm"]),
                alto_cubierto_mm=None if stats_payload.get("alto_cubierto_mm") is None else float(stats_payload["alto_cubierto_mm"]),
            ),
            plano=None if plane_payload is None else PlaneFit(
                a=float(plane_payload["a"]),
                b=float(plane_payload["b"]),
                c=float(plane_payload["c"]),
                inclinacion_x_mm_por_mm=float(plane_payload["inclinacion_x_mm_por_mm"]),
                inclinacion_y_mm_por_mm=float(plane_payload["inclinacion_y_mm_por_mm"]),
                rms_residuos_mm=float(plane_payload["rms_residuos_mm"]),
                residuo_maximo_mm=float(plane_payload["residuo_maximo_mm"]),
                residuo_minimo_mm=float(plane_payload["residuo_minimo_mm"]),
            ),
            creado_en=datetime.fromisoformat(str(payload["creado_en"])),
            actualizado_en=datetime.fromisoformat(str(payload["actualizado_en"])),
        )


