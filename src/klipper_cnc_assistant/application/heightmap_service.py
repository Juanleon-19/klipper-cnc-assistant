from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math

from klipper_cnc_assistant.domain import MaterialBruto, OperationPreparation, ProjectValidationError
from klipper_cnc_assistant.heightmap import (
    ExclusionZone,
    HeightGrid,
    HeightMap,
    HeightMapStatistics,
    HeightSample,
    PlaneFit,
    ProbeRegion,
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        probe_region: ProbeRegion,
        exclusion_zones: tuple[ExclusionZone, ...],
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        self._validate_domain(project.material, probe_region, exclusion_zones, filas, columnas)
        grid = self._grid_from_region(probe_region, filas, columnas)
        self._validate_grid_points(grid, probe_region, exclusion_zones)
        current = self._try_get_map(project_id, operation_id)
        if current and self._maps_are_geometry_compatible(current, grid, probe_region, exclusion_zones):
            samples = self._clone_samples_for_grid(current)
            height_map = compute_height_map(
                proyecto_id=project_id,
                operacion_id=operation_id,
                version=1 if current is None else current.version + 1,
                fuente_datos=current.fuente_datos,
                superficie_simulada=current.superficie_simulada,
                repeticion_simulacion=current.repeticion_simulacion,
                etiqueta_simulada=current.etiqueta_simulada,
                grid=grid,
                probe_region=probe_region,
                exclusion_zones=exclusion_zones,
                muestras=samples,
                estado=current.estado,
            )
            reason = "Se invalidó la preparación porque se volvió a confirmar la región sondeable."
        else:
            samples = self._blank_samples(grid, probe_region)
            height_map = compute_height_map(
                proyecto_id=project_id,
                operacion_id=operation_id,
                version=1 if current is None else current.version + 1,
                fuente_datos="manual",
                superficie_simulada=None,
                repeticion_simulacion=None,
                etiqueta_simulada=False,
                grid=grid,
                probe_region=probe_region,
                exclusion_zones=exclusion_zones,
                muestras=samples,
                estado="region sondeable configurada",
            )
            reason = "Se invalidó la preparación porque cambió la región sondeable."
        height_map = self._save_map(project_id, operation_id, replace(height_map, storage_revision=0 if current is None else current.storage_revision))
        if self._has_measured_data(height_map):
            self._mark_map_available(project_id, operation_id, reason=reason)
        else:
            self._update_preparation(
                project_id,
                operation_id,
                lambda current_prep: replace(
                    current_prep,
                    region_sondeable_configurada_en=utc_now(),
                    mapa_disponible_en=None,
                    mapa_validado_en=None,
                    compensacion_previsualizada_en=None,
                    motivo_invalidacion=reason,
                ),
            )
        return height_map

    def generate_simulated_map(
        self,
        *,
        project_id: str,
        operation_id: str,
        filas: int,
        columnas: int,
        superficie_simulada: str,
        repeticion_simulacion: int,
        probe_region: ProbeRegion,
        exclusion_zones: tuple[ExclusionZone, ...],
    ) -> HeightMap:
        project = self._load_project(project_id)
        project.get_operation(operation_id)
        self._validate_domain(project.material, probe_region, exclusion_zones, filas, columnas)
        grid = self._grid_from_region(probe_region, filas, columnas)
        self._validate_grid_points(grid, probe_region, exclusion_zones)
        current = self._try_get_map(project_id, operation_id)
        height_map = generate_simulated_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=1 if current is None else current.version + 1,
            probe_region=probe_region,
            exclusion_zones=exclusion_zones,
            filas=filas,
            columnas=columnas,
            superficie_simulada=superficie_simulada,
            repeticion_simulacion=repeticion_simulacion,
        )
        height_map = self._save_map(project_id, operation_id, replace(height_map, storage_revision=0 if current is None else current.storage_revision))
        self._mark_map_available(project_id, operation_id, reason="Se invalidó la preparación porque se generó un nuevo mapa simulado.")
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
        grid, probe_region, exclusion_zones, samples = parse_json_samples(content)
        normalized_grid = self._normalize_grid(project.material, probe_region, exclusion_zones, grid, samples)
        self._validate_imported_samples(normalized_grid, probe_region, samples)
        self._validate_grid_points(normalized_grid, probe_region, exclusion_zones)
        current = self._try_get_map(project_id, operation_id)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=1 if current is None else current.version + 1,
            fuente_datos="json",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=normalized_grid,
            probe_region=probe_region,
            exclusion_zones=exclusion_zones,
            muestras=samples,
            estado="importado",
        )
        height_map = self._save_map(project_id, operation_id, replace(height_map, storage_revision=0 if current is None else current.storage_revision))
        self._mark_map_available(project_id, operation_id, reason="Se invalidó la preparación porque se importó un mapa nuevo.")
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
        grid, probe_region, exclusion_zones, samples = parse_csv_samples(content)
        normalized_grid = self._normalize_grid(project.material, probe_region, exclusion_zones, grid, samples)
        self._validate_imported_samples(normalized_grid, probe_region, samples)
        self._validate_grid_points(normalized_grid, probe_region, exclusion_zones)
        current = self._try_get_map(project_id, operation_id)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=1 if current is None else current.version + 1,
            fuente_datos="csv",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=normalized_grid,
            probe_region=probe_region,
            exclusion_zones=exclusion_zones,
            muestras=samples,
            estado="importado",
        )
        height_map = self._save_map(project_id, operation_id, replace(height_map, storage_revision=0 if current is None else current.storage_revision))
        self._mark_map_available(project_id, operation_id, reason="Se invalidó la preparación porque se importó un mapa nuevo.")
        return height_map

    def get_map(self, project_id: str, operation_id: str) -> HeightMap:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        map_key = operation.setup_id
        try:
            payload = self.repository.load_height_map_payload(project_id, map_key)
        except FileNotFoundError:
            payload = self._load_legacy_map(project, operation_id)
            if payload is None:
                raise NotFoundError(
                    f"El mapa de alturas para el montaje {map_key} no existe."
                )
        return self._deserialize_map(payload)

    def _load_legacy_map(self, project, operation_id: str) -> dict | None:
        operation = project.get_operation(operation_id)
        candidates = (operation_id,) + tuple(
            item.id
            for item in project.operations_for_setup(operation.setup_id)
            if item.id != operation_id
        )
        for candidate in candidates:
            try:
                return self.repository.load_height_map_payload(project.id, candidate)
            except FileNotFoundError:
                continue
        return None

    def get_statistics(self, project_id: str, operation_id: str):
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
                next_sample = replace(next_sample, z_mm=z_mm, estado_calidad=SampleQuality.FALTANTE if z_mm is None else SampleQuality.VALIDA)
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
        recalculated = self._rebuild(current, updated_samples)
        recalculated = self._save_map(project_id, operation_id, recalculated)
        self._mark_map_available(project_id, operation_id, reason="Se invalidó la preparación porque cambió el contenido del mapa.")
        return recalculated

    def recalculate_map(self, project_id: str, operation_id: str) -> HeightMap:
        current = self.get_map(project_id, operation_id)
        recalculated = self._rebuild(current, list(current.muestras))
        recalculated = self._save_map(project_id, operation_id, recalculated)
        self._mark_map_available(project_id, operation_id, reason="Se invalidó la preparación porque se recalculó el mapa.")
        return recalculated

    def validate_map(self, project_id: str, operation_id: str) -> HeightMap:
        current = self.get_map(project_id, operation_id)
        included = [sample for sample in current.muestras if sample.incluida and sample.z_mm is not None]
        if len(included) < 3:
            raise ApplicationError("El mapa requiere al menos 3 muestras validas para poder validarse.")
        self._update_preparation(
            project_id,
            operation_id,
            lambda prep: replace(prep, mapa_validado_en=utc_now(), compensacion_previsualizada_en=None, motivo_invalidacion=None),
        )
        return current

    def delete_map(self, project_id: str, operation_id: str) -> None:
        self._load_project(project_id).get_operation(operation_id)
        try:
            self.repository.delete_height_map(project_id, self._map_key(project_id, operation_id))
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        self._update_preparation(project_id, operation_id, lambda _: OperationPreparation())

    def build_surfaces(self, height_map: HeightMap) -> dict[str, dict[str, object]]:
        return {
            "bruto": build_dense_surface(height_map, mode="bruto"),
            "plano": build_dense_surface(height_map, mode="plano"),
            "residuo": build_dense_surface(height_map, mode="residuo"),
        }

    def _rebuild(self, current: HeightMap, samples: list[HeightSample]) -> HeightMap:
        rebuilt = compute_height_map(
            proyecto_id=current.proyecto_id,
            operacion_id=current.operacion_id,
            version=current.version + 1,
            fuente_datos=current.fuente_datos,
            superficie_simulada=current.superficie_simulada,
            repeticion_simulacion=current.repeticion_simulacion,
            etiqueta_simulada=current.etiqueta_simulada,
            grid=current.grid,
            probe_region=current.probe_region,
            exclusion_zones=current.exclusion_zones,
            muestras=samples,
            estado=current.estado,
        )
        return replace(rebuilt, storage_revision=current.storage_revision)

    def _save_map(self, project_id: str, operation_id: str, height_map: HeightMap) -> HeightMap:
        payload = self._serialize_map(height_map)
        self.repository.save_height_map_payload(project_id, self._map_key(project_id, operation_id), payload)
        return self._deserialize_map(payload)

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error

    def _map_key(self, project_id: str, operation_id: str) -> str:
        project = self._load_project(project_id)
        return project.get_operation(operation_id).setup_id

    def _grid_from_region(self, probe_region: ProbeRegion, filas: int, columnas: int) -> HeightGrid:
        if filas < 1 or columnas < 1:
            raise ApplicationError("La malla del mapa requiere al menos 1 fila y 1 columna.")
        return HeightGrid(
            filas=filas,
            columnas=columnas,
            ancho_mm=probe_region.ancho_mm,
            alto_mm=probe_region.alto_mm,
            paso_x_mm=0.0 if columnas == 1 else probe_region.ancho_mm / (columnas - 1),
            paso_y_mm=0.0 if filas == 1 else probe_region.alto_mm / (filas - 1),
        )

    def _blank_samples(self, grid: HeightGrid, probe_region: ProbeRegion) -> list[HeightSample]:
        samples: list[HeightSample] = []
        for fila in range(grid.filas):
            for columna in range(grid.columnas):
                samples.append(
                    HeightSample(
                        id=f"hm_{fila}_{columna}",
                        x_mm=probe_region.min_x_mm + columna * grid.paso_x_mm,
                        y_mm=probe_region.min_y_mm + fila * grid.paso_y_mm,
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
        material: MaterialBruto,
        probe_region: ProbeRegion,
        exclusion_zones: tuple[ExclusionZone, ...],
        grid: HeightGrid,
        samples: list[HeightSample],
    ) -> HeightGrid:
        rows = grid.filas or max(sample.fila for sample in samples) + 1
        columns = grid.columnas or max(sample.columna for sample in samples) + 1
        self._validate_domain(material, probe_region, exclusion_zones, rows, columns)
        width = grid.ancho_mm or probe_region.ancho_mm
        height = grid.alto_mm or probe_region.alto_mm
        step_x = grid.paso_x_mm or (0.0 if columns == 1 else width / (columns - 1))
        step_y = grid.paso_y_mm or (0.0 if rows == 1 else height / (rows - 1))
        return HeightGrid(
            filas=rows,
            columnas=columns,
            ancho_mm=width,
            alto_mm=height,
            paso_x_mm=step_x,
            paso_y_mm=step_y,
        )

    def _validate_domain(
        self,
        material: MaterialBruto,
        probe_region: ProbeRegion,
        exclusion_zones: tuple[ExclusionZone, ...],
        filas: int,
        columnas: int,
    ) -> None:
        if probe_region.ancho_mm < 0 or probe_region.alto_mm < 0:
            raise ApplicationError("La region sondeable debe tener dimensiones validas.")
        if probe_region.ancho_mm == 0 and columnas != 1:
            raise ApplicationError("Una region sin ancho requiere exactamente 1 columna.")
        if probe_region.alto_mm == 0 and filas != 1:
            raise ApplicationError("Una region sin alto requiere exactamente 1 fila.")
        if probe_region.ancho_mm == 0 and probe_region.alto_mm == 0 and (filas, columnas) != (1, 1):
            raise ApplicationError("Una region puntual requiere una malla 1 x 1.")
        if probe_region.min_x_mm < 0 or probe_region.min_y_mm < 0:
            raise ApplicationError("La region sondeable debe estar dentro del material.")
        if probe_region.max_x_mm > material.ancho_mm or probe_region.max_y_mm > material.alto_mm:
            raise ApplicationError("La region sondeable debe estar dentro del material.")
        if filas < 1 or columnas < 1:
            raise ApplicationError("La malla del mapa requiere al menos 1 fila y 1 columna.")
        for zone in exclusion_zones:
            if zone.max_x_mm <= zone.min_x_mm or zone.max_y_mm <= zone.min_y_mm:
                raise ApplicationError(f"La zona excluida '{zone.nombre}' no tiene dimensiones validas.")
            if zone.min_x_mm < 0 or zone.min_y_mm < 0 or zone.max_x_mm > material.ancho_mm or zone.max_y_mm > material.alto_mm:
                raise ApplicationError(f"La zona excluida '{zone.nombre}' debe estar dentro del material.")

    def _validate_imported_samples(self, grid: HeightGrid, region: ProbeRegion, samples: list[HeightSample]) -> None:
        """The interpolator indexes a regular grid, never an arbitrary point cloud."""
        seen_slots: set[tuple[int, int]] = set()
        seen_ids: set[str] = set()
        expected_dx = region.ancho_mm / (grid.columnas - 1) if grid.columnas > 1 else 0.0
        expected_dy = region.alto_mm / (grid.filas - 1) if grid.filas > 1 else 0.0
        for actual, expected in ((grid.paso_x_mm, expected_dx), (grid.paso_y_mm, expected_dy),
                                 (grid.ancho_mm, region.ancho_mm), (grid.alto_mm, region.alto_mm)):
            if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6):
                raise ApplicationError("El mapa importado debe describir una malla regular coherente con su región.")
        for sample in samples:
            slot = (sample.fila, sample.columna)
            if slot in seen_slots or sample.id in seen_ids:
                raise ApplicationError("El mapa importado contiene una muestra o posición de malla repetida.")
            seen_slots.add(slot)
            seen_ids.add(sample.id)
            expected_x = region.min_x_mm + sample.columna * grid.paso_x_mm
            expected_y = region.min_y_mm + sample.fila * grid.paso_y_mm
            if (not 0 <= sample.fila < grid.filas or not 0 <= sample.columna < grid.columnas
                    or not math.isclose(sample.x_mm, expected_x, rel_tol=0, abs_tol=1e-6)
                    or not math.isclose(sample.y_mm, expected_y, rel_tol=0, abs_tol=1e-6)):
                raise ApplicationError("Las coordenadas importadas no corresponden a su posición en la malla regular.")

    def _validate_grid_points(self, grid: HeightGrid, probe_region: ProbeRegion, exclusion_zones: tuple[ExclusionZone, ...]) -> None:
        for fila in range(grid.filas):
            for columna in range(grid.columnas):
                x_mm = probe_region.min_x_mm + columna * grid.paso_x_mm
                y_mm = probe_region.min_y_mm + fila * grid.paso_y_mm
                for zone in exclusion_zones:
                    if zone.contains(x_mm, y_mm):
                        raise ApplicationError(
                            f"La configuracion actual ubica el punto ({x_mm:.3f}, {y_mm:.3f}) mm dentro de la zona excluida '{zone.nombre}'."
                        )

    def _mark_map_available(self, project_id: str, operation_id: str, *, reason: str | None = None) -> None:
        now = utc_now()
        self._update_preparation(
            project_id,
            operation_id,
            lambda current: replace(
                current,
                region_sondeable_configurada_en=current.region_sondeable_configurada_en or now,
                mapa_disponible_en=now,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=reason,
            ),
        )

    def _update_preparation(self, project_id: str, operation_id: str, updater) -> None:
        project = self._load_project(project_id)
        setup = project.setup_for_operation(operation_id)
        updated_setup = replace(setup, preparacion=updater(setup.preparacion))
        self.repository.save_project(project.replace_setup(updated_setup))

    def _try_get_map(self, project_id: str, operation_id: str) -> HeightMap | None:
        try:
            return self.get_map(project_id, operation_id)
        except NotFoundError:
            return None

    def _maps_are_geometry_compatible(
        self,
        current: HeightMap,
        grid: HeightGrid,
        probe_region: ProbeRegion,
        exclusion_zones: tuple[ExclusionZone, ...],
    ) -> bool:
        if current.grid.filas != grid.filas or current.grid.columnas != grid.columnas:
            return False
        if current.probe_region != probe_region:
            return False
        if current.exclusion_zones != exclusion_zones:
            return False
        return all(
            sample.x_mm == probe_region.min_x_mm + sample.columna * grid.paso_x_mm
            and sample.y_mm == probe_region.min_y_mm + sample.fila * grid.paso_y_mm
            for sample in current.muestras
        )

    def _clone_samples_for_grid(self, current: HeightMap) -> list[HeightSample]:
        return [replace(sample) for sample in current.muestras]

    def _has_measured_data(self, height_map: HeightMap) -> bool:
        return any(sample.z_mm is not None for sample in height_map.muestras)

    def _serialize_map(self, height_map: HeightMap) -> dict[str, object]:
        return {
            "storage_revision": height_map.storage_revision,
            "proyecto_id": height_map.proyecto_id,
            "operacion_id": height_map.operacion_id,
            "version": height_map.version,
            "version_algoritmo": height_map.version_algoritmo,
            "estado": height_map.estado,
            "fuente_datos": height_map.fuente_datos,
            "superficie_simulada": height_map.superficie_simulada,
            "repeticion_simulacion": height_map.repeticion_simulacion,
            "etiqueta_simulada": height_map.etiqueta_simulada,
            "grid": {
                "filas": height_map.grid.filas,
                "columnas": height_map.grid.columnas,
                "ancho_mm": height_map.grid.ancho_mm,
                "alto_mm": height_map.grid.alto_mm,
                "paso_x_mm": height_map.grid.paso_x_mm,
                "paso_y_mm": height_map.grid.paso_y_mm,
            },
            "probe_region": {
                "min_x_mm": height_map.probe_region.min_x_mm,
                "min_y_mm": height_map.probe_region.min_y_mm,
                "max_x_mm": height_map.probe_region.max_x_mm,
                "max_y_mm": height_map.probe_region.max_y_mm,
            },
            "exclusion_zones": [
                {
                    "id": zone.id,
                    "nombre": zone.nombre,
                    "min_x_mm": zone.min_x_mm,
                    "min_y_mm": zone.min_y_mm,
                    "max_x_mm": zone.max_x_mm,
                    "max_y_mm": zone.max_y_mm,
                }
                for zone in height_map.exclusion_zones
            ],
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
                "valor_referencia_mm": height_map.estadisticas.valor_referencia_mm,
                "desviacion_rms_respecto_plano_mm": height_map.estadisticas.desviacion_rms_respecto_plano_mm,
                "residuo_maximo_mm": height_map.estadisticas.residuo_maximo_mm,
                "ancho_cubierto_mm": height_map.estadisticas.ancho_cubierto_mm,
                "alto_cubierto_mm": height_map.estadisticas.alto_cubierto_mm,
            },
            "plano": None
            if height_map.plano is None
            else {
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
        grid_payload = payload["grid"]
        probe_payload = payload.get("probe_region") or {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": grid_payload["ancho_mm"],
            "max_y_mm": grid_payload["alto_mm"],
        }
        exclusion_payload = payload.get("exclusion_zones", [])
        statistics_payload = payload["estadisticas"]
        plane_payload = payload.get("plano")
        return HeightMap(
            storage_revision=payload.get("storage_revision", 0),
            proyecto_id=payload["proyecto_id"],
            operacion_id=payload["operacion_id"],
            version=payload["version"],
            version_algoritmo=payload["version_algoritmo"],
            estado=payload["estado"],
            fuente_datos=payload["fuente_datos"],
            superficie_simulada=payload.get("superficie_simulada", payload.get("escenario")),
            repeticion_simulacion=payload.get("repeticion_simulacion", payload.get("semilla")),
            etiqueta_simulada=payload["etiqueta_simulada"],
            grid=HeightGrid(**grid_payload),
            probe_region=ProbeRegion(**probe_payload),
            exclusion_zones=tuple(ExclusionZone(**item) for item in exclusion_payload),
            muestras=tuple(
                HeightSample(
                    id=item["id"],
                    x_mm=item["x_mm"],
                    y_mm=item["y_mm"],
                    z_mm=item.get("z_mm"),
                    fila=item["fila"],
                    columna=item["columna"],
                    origen_datos=item.get("origen_datos", "manual"),
                    estado_calidad=SampleQuality(item.get("estado_calidad", SampleQuality.VALIDA)),
                    observacion=item.get("observacion"),
                    incluida=item.get("incluida", True),
                    residuo_plano_mm=item.get("residuo_plano_mm"),
                )
                for item in payload["muestras"]
            ),
            estadisticas=HeightMapStatistics(
                cantidad_puntos=statistics_payload["cantidad_puntos"],
                cantidad_puntos_incluidos=statistics_payload["cantidad_puntos_incluidos"],
                cantidad_puntos_faltantes=statistics_payload["cantidad_puntos_faltantes"],
                cantidad_puntos_atipicos=statistics_payload["cantidad_puntos_atipicos"],
                altura_min_mm=statistics_payload.get("altura_min_mm"),
                altura_max_mm=statistics_payload.get("altura_max_mm"),
                rango_alturas_mm=statistics_payload.get("rango_alturas_mm"),
                valor_referencia_mm=statistics_payload.get("valor_referencia_mm"),
                desviacion_rms_respecto_plano_mm=statistics_payload.get("desviacion_rms_respecto_plano_mm", statistics_payload.get("rms_residuos_mm")),
                residuo_maximo_mm=statistics_payload.get("residuo_maximo_mm"),
                ancho_cubierto_mm=statistics_payload.get("ancho_cubierto_mm"),
                alto_cubierto_mm=statistics_payload.get("alto_cubierto_mm"),
            ),
            plano=None
            if plane_payload is None
            else PlaneFit(
                a=plane_payload["a"],
                b=plane_payload["b"],
                c=plane_payload["c"],
                inclinacion_x_mm_por_mm=plane_payload["inclinacion_x_mm_por_mm"],
                inclinacion_y_mm_por_mm=plane_payload["inclinacion_y_mm_por_mm"],
                rms_residuos_mm=plane_payload["rms_residuos_mm"],
                residuo_maximo_mm=plane_payload["residuo_maximo_mm"],
                residuo_minimo_mm=plane_payload["residuo_minimo_mm"],
            ),
            creado_en=datetime.fromisoformat(payload["creado_en"]),
            actualizado_en=datetime.fromisoformat(payload["actualizado_en"]),
        )
