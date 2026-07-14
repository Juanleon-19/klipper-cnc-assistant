from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from klipper_cnc_assistant.domain import CoordinateReference, PreparationState, ProjectValidationError
from klipper_cnc_assistant.heightmap.compensation import build_compensation_preview
from klipper_cnc_assistant.storage import JsonProjectRepository

from .errors import ApplicationError, NotFoundError
from .heightmap_service import HeightMapService
from .services import MachineSessionService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReferenceSessionService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        height_map_service: HeightMapService,
        machine_session_service: MachineSessionService,
    ) -> None:
        self.repository = repository
        self.height_map_service = height_map_service
        self.machine_session_service = machine_session_service

    def get_session(self, project_id: str, operation_id: str) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        machine = self.machine_session_service.get_status()
        return self._build_session_payload(project, operation, machine)

    def confirm_machine_reference(self, project_id: str, operation_id: str) -> dict[str, object]:
        self._load_project(project_id).get_operation(operation_id)
        self.machine_session_service.confirm_reference_in_simulation()
        return self.get_session(project_id, operation_id)

    def confirm_work_origin(self, project_id: str, operation_id: str, *, x_mm: float, y_mm: float) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        machine = self.machine_session_service.get_status()
        if not machine.home_realizado:
            raise ApplicationError("Primero confirme la referencia de maquina en simulacion.")
        self._validate_xy_against_material(project.material.ancho_mm, project.material.alto_mm, x_mm, y_mm, "origen de trabajo")
        timestamp = utc_now()
        updated = replace(
            operation,
            preparacion=replace(
                operation.preparacion,
                origen_trabajo=CoordinateReference(x_mm=x_mm, y_mm=y_mm, confirmado_en=timestamp),
                referencia_z=None,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
            ),
        )
        self.repository.save_project(project.replace_operation(updated))
        return self.get_session(project_id, operation_id)

    def confirm_z_reference(
        self,
        project_id: str,
        operation_id: str,
        *,
        x_mm: float,
        y_mm: float,
        z_mm: float,
    ) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        machine = self.machine_session_service.get_status()
        if not machine.home_realizado:
            raise ApplicationError("Primero confirme la referencia de maquina en simulacion.")
        if operation.preparacion.origen_trabajo is None:
            raise ApplicationError("Primero confirme el origen de trabajo X/Y en simulacion.")
        self._validate_xy_against_material(project.material.ancho_mm, project.material.alto_mm, x_mm, y_mm, "referencia Z")
        timestamp = utc_now()
        updated = replace(
            operation,
            preparacion=replace(
                operation.preparacion,
                referencia_z=CoordinateReference(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, confirmado_en=timestamp),
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
            ),
        )
        self.repository.save_project(project.replace_operation(updated))
        return self.get_session(project_id, operation_id)

    def mark_map_validated(self, project_id: str, operation_id: str) -> dict[str, object]:
        self.height_map_service.validate_map(project_id, operation_id)
        return self.get_session(project_id, operation_id)

    def build_compensation_preview(self, project_id: str, operation_id: str) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        machine = self.machine_session_service.get_status()
        if not machine.home_realizado or operation.preparacion.origen_trabajo is None or operation.preparacion.referencia_z is None:
            raise ApplicationError("La previsualizacion requiere referencia de maquina, origen X/Y y referencia Z confirmados en simulacion.")
        if operation.preparacion.mapa_validado_en is None:
            raise ApplicationError("La previsualizacion requiere un mapa validado.")
        if operation.analisis is None:
            raise ApplicationError("La operacion requiere un analisis G-code antes de previsualizar la compensacion.")
        height_map = self.height_map_service.get_map(project_id, operation_id)
        preview = build_compensation_preview(
            analysis=operation.analisis,
            height_map=height_map,
            reference_z_mm=operation.preparacion.referencia_z.z_mm or 0.0,
        )
        updated = replace(
            operation,
            preparacion=replace(operation.preparacion, compensacion_previsualizada_en=utc_now()),
        )
        self.repository.save_project(project.replace_operation(updated))
        session = self.get_session(project_id, operation_id)
        return {"session": session, "preview": preview}

    def _build_session_payload(self, project, operation, machine) -> dict[str, object]:
        prep = operation.preparacion
        state = self._derive_state(operation.preparacion, machine.home_realizado)
        gcode_origin = self._build_gcode_origin(operation.analisis)
        steps = [
            {
                "id": "referencia_maquina",
                "titulo": "Referencia de maquina",
                "confirmado": machine.home_realizado,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
            },
            {
                "id": "origen_xy",
                "titulo": "Origen de trabajo X/Y",
                "confirmado": prep.origen_trabajo is not None,
                "fecha": None if prep.origen_trabajo is None or prep.origen_trabajo.confirmado_en is None else prep.origen_trabajo.confirmado_en.isoformat(),
            },
            {
                "id": "referencia_z",
                "titulo": "Referencia Z",
                "confirmado": prep.referencia_z is not None,
                "fecha": None if prep.referencia_z is None or prep.referencia_z.confirmado_en is None else prep.referencia_z.confirmado_en.isoformat(),
            },
            {
                "id": "region_sondeable",
                "titulo": "Region sondeable",
                "confirmado": prep.region_sondeable_configurada_en is not None,
                "fecha": None if prep.region_sondeable_configurada_en is None else prep.region_sondeable_configurada_en.isoformat(),
            },
            {
                "id": "mapa",
                "titulo": "Mapa",
                "confirmado": prep.mapa_disponible_en is not None,
                "fecha": None if prep.mapa_disponible_en is None else prep.mapa_disponible_en.isoformat(),
            },
            {
                "id": "validacion",
                "titulo": "Validacion",
                "confirmado": prep.mapa_validado_en is not None,
                "fecha": None if prep.mapa_validado_en is None else prep.mapa_validado_en.isoformat(),
            },
        ]
        return {
            "estado": state,
            "machine_reference": {
                "confirmada": machine.home_realizado,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
            },
            "origen_maquina": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            "origen_material": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            "origen_gcode": gcode_origin,
            "origen_trabajo": self._serialize_reference(prep.origen_trabajo),
            "referencia_z": self._serialize_reference(prep.referencia_z),
            "pasos": steps,
            "compensacion_previsualizada_en": None if prep.compensacion_previsualizada_en is None else prep.compensacion_previsualizada_en.isoformat(),
            "analysis_stale": bool(operation.analisis and operation.analisis.analisis_desactualizado),
        }

    def _derive_state(self, preparation, machine_reference_confirmed: bool) -> str:
        if not machine_reference_confirmed:
            if preparation.origen_trabajo or preparation.referencia_z or preparation.region_sondeable_configurada_en:
                return PreparationState.REFERENCIA_MAQUINA_PENDIENTE
            return PreparationState.SIN_INICIAR
        if preparation.origen_trabajo is None:
            return PreparationState.ORIGEN_XY_PENDIENTE
        if preparation.referencia_z is None:
            return PreparationState.REFERENCIA_Z_PENDIENTE
        if preparation.region_sondeable_configurada_en is None:
            return PreparationState.REFERENCIA_Z_CONFIRMADA
        if preparation.mapa_disponible_en is None:
            return PreparationState.REGION_SONDEABLE_CONFIGURADA
        if preparation.mapa_validado_en is None:
            return PreparationState.MAPA_DISPONIBLE
        if preparation.compensacion_previsualizada_en is None:
            return PreparationState.MAPA_VALIDADO
        return PreparationState.COMPENSACION_PREVISUALIZADA

    def _build_gcode_origin(self, analysis) -> dict[str, float | None]:
        if analysis is None or not analysis.segmentos_vista_previa:
            return {"x_mm": None, "y_mm": None, "z_mm": None}
        first = analysis.segmentos_vista_previa[0]
        return {"x_mm": first.inicio_x_mm, "y_mm": first.inicio_y_mm, "z_mm": first.z_mm}

    def _serialize_reference(self, reference: CoordinateReference | None) -> dict[str, float | str | None] | None:
        if reference is None:
            return None
        return {
            "x_mm": reference.x_mm,
            "y_mm": reference.y_mm,
            "z_mm": reference.z_mm,
            "fecha": None if reference.confirmado_en is None else reference.confirmado_en.isoformat(),
        }

    def _validate_xy_against_material(self, material_x: float, material_y: float, x_mm: float, y_mm: float, label: str) -> None:
        if x_mm < 0 or y_mm < 0 or x_mm > material_x or y_mm > material_y:
            raise ApplicationError(f"La coordenada de {label} debe quedar dentro del material.")

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
