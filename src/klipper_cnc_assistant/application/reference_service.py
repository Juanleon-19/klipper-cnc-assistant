from __future__ import annotations

from dataclasses import replace
from typing import Any
from datetime import datetime, timezone

from klipper_cnc_assistant.domain import CapturedPosition, CoordinateReference, PreparationState, ProjectValidationError
from klipper_cnc_assistant.heightmap.compensation import build_compensation_preview
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report
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
        physical_map_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.height_map_service = height_map_service
        self.machine_session_service = machine_session_service
        self.physical_map_service = physical_map_service

    def get_session(self, project_id: str, operation_id: str) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        machine = self.machine_session_service.get_status()
        return self._build_session_payload(project, operation, setup, machine)

    def confirm_machine_reference(self, project_id: str, operation_id: str) -> dict[str, object]:
        self._load_project(project_id).get_operation(operation_id)
        self.machine_session_service.confirm_reference_in_simulation()
        return self.get_session(project_id, operation_id)

    def confirm_work_origin(self, project_id: str, operation_id: str, *, x_mm: float, y_mm: float) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        machine = self.machine_session_service.get_status()
        if not machine.home_realizado:
            raise ApplicationError("Primero confirme la referencia de maquina en simulacion.")
        self._validate_xy_against_material(project.material.ancho_mm, project.material.alto_mm, x_mm, y_mm, "origen de trabajo")
        self._reject_simulated_overwrite_of_measured(setup.preparacion.origen_trabajo, "origen de trabajo X/Y")
        timestamp = utc_now()
        invalidation = self._build_invalidation_reason(
            setup.preparacion,
            "Se invalidó la preparación porque cambió el origen de trabajo X/Y.",
        )
        updated = replace(
            setup,
            preparacion=replace(
                setup.preparacion,
                origen_trabajo=CoordinateReference(x_mm=x_mm, y_mm=y_mm, confirmado_en=timestamp),
                referencia_z=None,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=invalidation,
            ),
        )
        self.repository.save_project(project.replace_setup(updated))
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
        setup = project.get_setup(operation.setup_id)
        machine = self.machine_session_service.get_status()
        if not machine.home_realizado:
            raise ApplicationError("Primero confirme la referencia de maquina en simulacion.")
        if setup.preparacion.origen_trabajo is None:
            raise ApplicationError("Primero confirme el origen de trabajo X/Y en simulacion.")
        self._validate_xy_against_material(project.material.ancho_mm, project.material.alto_mm, x_mm, y_mm, "referencia Z")
        self._reject_simulated_overwrite_of_measured(setup.preparacion.referencia_z, "referencia Z")
        timestamp = utc_now()
        invalidation = self._build_invalidation_reason(
            setup.preparacion,
            "Se invalidó la preparación porque cambió la referencia Z.",
        )
        updated = replace(
            setup,
            preparacion=replace(
                setup.preparacion,
                referencia_z=CoordinateReference(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, confirmado_en=timestamp),
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=invalidation,
            ),
        )
        self.repository.save_project(project.replace_setup(updated))
        return self.get_session(project_id, operation_id)

    def capture_physical_work_origin(
        self,
        project_id: str,
        operation_id: str,
        *,
        position: dict[str, float],
        machine_label: str,
        homed_axes: str | None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        timestamp = utc_now()
        invalidation = self._build_invalidation_reason(
            setup.preparacion,
            "Se invalidó la preparación porque cambió el origen físico X/Y del montaje.",
        )
        reference = CoordinateReference(
            x_mm=float(position["x_mm"]),
            y_mm=float(position["y_mm"]),
            z_mm=None,
            confirmado_en=timestamp,
            fuente="MEASURED",
            maquina=machine_label,
            homed_axes=homed_axes,
            posicion_captura=self._captured_position_from_dict(position),
            sesion=session_id,
        )
        updated = replace(
            setup,
            preparacion=replace(
                setup.preparacion,
                origen_trabajo=reference,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=invalidation,
            ),
        )
        self.repository.save_project(project.replace_setup(updated))
        return self.get_session(project_id, operation_id)

    def capture_physical_z_reference(
        self,
        project_id: str,
        operation_id: str,
        *,
        position: dict[str, float],
        machine_label: str,
        homed_axes: str | None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        timestamp = utc_now()
        invalidation = self._build_invalidation_reason(
            setup.preparacion,
            "Se invalidó la preparación porque cambió la referencia física Z del montaje.",
        )
        reference = CoordinateReference(
            x_mm=float(position["x_mm"]),
            y_mm=float(position["y_mm"]),
            z_mm=float(position["z_mm"]),
            confirmado_en=timestamp,
            fuente="MEASURED",
            maquina=machine_label,
            homed_axes=homed_axes,
            posicion_captura=self._captured_position_from_dict(position),
            sesion=session_id,
        )
        updated = replace(
            setup,
            preparacion=replace(
                setup.preparacion,
                referencia_z=reference,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=invalidation,
            ),
        )
        self.repository.save_project(project.replace_setup(updated))
        return self.get_session(project_id, operation_id)

    def mark_map_validated(self, project_id: str, operation_id: str) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        height_map = self.height_map_service.get_map(project_id, operation_id)
        operations_with_analysis = tuple(
            (item.id, item.nombre, item.analisis)
            for item in project.operations_for_setup(setup.id)
            if item.analisis is not None
        )
        if operations_with_analysis:
            coverage = build_coverage_report(
                height_map=height_map,
                operations=operations_with_analysis,
                tolerance_mm=DOMAIN_TOLERANCE_MM,
            )
            if not coverage.sufficient:
                first = coverage.issues[0] if coverage.issues else None
                hint = "Amplie la region sondeable o corrija la referencia/origen del montaje."
                detail = (
                    f" Primer punto: operacion {first.operation_name}, "
                    f"X={first.x_mm:.3f} mm, Y={first.y_mm:.3f} mm, "
                    f"distancia={first.distance_mm:.3f} mm, causa={first.reason}."
                    if first
                    else ""
                )
                raise ApplicationError(
                    f"La cobertura del mapa es insuficiente: {coverage.blocking_outside_points} puntos quedan fuera del dominio medido. "
                    f"Tolerancia numerica {coverage.tolerance_mm:.6f} mm. {hint}{detail}"
                )
        self.height_map_service.validate_map(project_id, operation_id)
        return self.get_session(project_id, operation_id)

    def build_compensation_preview(self, project_id: str, operation_id: str) -> dict[str, object]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        machine = self.machine_session_service.get_status()
        if self._is_physical_machine(machine):
            context = self._physical_context(project_id, operation, setup, machine)
            blocks = self._build_compensation_blocks_from_context(context)
            if blocks:
                raise ApplicationError("La previsualizacion sigue bloqueada. " + " ".join(blocks))
            physical_map = context.get("physical_map")
            if physical_map is not None and self._is_complete_measured_map(physical_map):
                height_map = self.height_map_service._deserialize_map(physical_map["height_map"])
            else:
                height_map = self.height_map_service.get_map(project_id, operation_id)
            reference_z = context.get("referencia_z")
        else:
            blocks = self._build_compensation_blocks(setup.preparacion, machine.home_realizado)
            if blocks:
                raise ApplicationError("La previsualizacion sigue bloqueada. " + " ".join(blocks))
            height_map = self.height_map_service.get_map(project_id, operation_id)
            reference_z = setup.preparacion.referencia_z
        if operation.analisis is None:
            raise ApplicationError("La operacion requiere un analisis G-code antes de previsualizar la compensacion.")
        preview = build_compensation_preview(
            analysis=operation.analisis,
            height_map=height_map,
            reference_z_mm=reference_z.z_mm if reference_z and reference_z.z_mm is not None else 0.0,
            operation_id=operation.id,
            operation_name=operation.nombre,
        )
        updated = replace(
            setup,
            preparacion=replace(setup.preparacion, compensacion_previsualizada_en=utc_now(), motivo_invalidacion=None),
        )
        self.repository.save_project(project.replace_setup(updated))
        session = self.get_session(project_id, operation_id)
        return {"session": session, "preview": preview}

    def _is_physical_machine(self, machine) -> bool:
        return str(getattr(machine, "estado", "")).startswith("fisica")

    def _build_session_payload(self, project, operation, setup, machine) -> dict[str, object]:
        if not self._is_physical_machine(machine):
            return self._build_legacy_session_payload(project, operation, setup, machine)
        prep = setup.preparacion
        context = self._physical_context(project.id, operation, setup, machine)
        machine_reference_confirmed = bool(context["machine_reference_confirmed"])
        origin_reference = context["origen_trabajo"]
        z_reference = context["referencia_z"]
        region_configured_at = context["region_sondeable_configurada_en"]
        map_available_at = context["mapa_disponible_en"]
        map_validated_at = context["mapa_validado_en"]
        state = self._derive_state_from_values(machine_reference_confirmed, origin_reference, z_reference, region_configured_at, map_available_at, map_validated_at, prep.compensacion_previsualizada_en)
        gcode_origin = self._build_gcode_origin(operation.analisis)
        ready_for_compensation = not self._build_compensation_blocks_from_context(context)
        map_step_state, map_step_detail = self._map_step_status_from_values(map_available_at, machine_reference_confirmed, origin_reference, z_reference, map_validated_at)
        validation_step_state, validation_step_detail = self._validation_step_status_from_values(map_available_at, map_validated_at, machine_reference_confirmed, origin_reference, z_reference, prep.motivo_invalidacion, context.get("physical_map"))
        steps = [
            {
                "id": "referencia_maquina",
                "titulo": "Referencia de maquina",
                "estado": "confirmado" if machine_reference_confirmed else "pendiente",
                "confirmado": machine_reference_confirmed,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
                "detalle": "Homing válido para la malla física medida." if machine_reference_confirmed else "Falta homing físico reportado por Klipper.",
            },
            {
                "id": "origen_xy",
                "titulo": "Origen de trabajo X/Y",
                "estado": "confirmado" if origin_reference is not None else "pendiente",
                "confirmado": origin_reference is not None,
                "fecha": None if origin_reference is None or origin_reference.confirmado_en is None else origin_reference.confirmado_en.isoformat(),
                "detalle": "Origen X/Y medido para la malla física." if origin_reference is not None else "Pendiente de referencia X/Y medida.",
            },
            {
                "id": "referencia_z",
                "titulo": "Referencia Z",
                "estado": "confirmado" if z_reference is not None else "pendiente",
                "confirmado": z_reference is not None,
                "fecha": None if z_reference is None or z_reference.confirmado_en is None else z_reference.confirmado_en.isoformat(),
                "detalle": "Referencia Z medida para la herramienta instalada." if z_reference is not None else "Pendiente de referencia Z medida.",
            },
            {
                "id": "region_sondeable",
                "titulo": "Region sondeable",
                "estado": "confirmado" if region_configured_at is not None else "pendiente",
                "confirmado": region_configured_at is not None,
                "fecha": None if region_configured_at is None else region_configured_at.isoformat(),
                "detalle": "Región configurada desde la malla física medida." if region_configured_at is not None else "Configure una región sondeable para definir el dominio interpolable.",
            },
            {
                "id": "mapa",
                "titulo": "Mapa",
                "estado": map_step_state,
                "confirmado": map_available_at is not None,
                "fecha": None if map_available_at is None else map_available_at.isoformat(),
                "detalle": map_step_detail,
            },
            {
                "id": "validacion",
                "titulo": "Validacion",
                "estado": validation_step_state,
                "confirmado": map_validated_at is not None,
                "fecha": None if map_validated_at is None else map_validated_at.isoformat(),
                "detalle": validation_step_detail,
            },
        ]
        return {
            "estado": state,
            "machine_reference": {
                "confirmada": machine_reference_confirmed,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
            },
            "origen_maquina": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            "origen_material": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            "origen_gcode": gcode_origin,
            "origen_trabajo": self._serialize_reference(origin_reference),
            "referencia_z": self._serialize_reference(z_reference),
            "pasos": steps,
            "compensacion_previsualizada_en": None if prep.compensacion_previsualizada_en is None else prep.compensacion_previsualizada_en.isoformat(),
            "analysis_stale": bool(operation.analisis and operation.analisis.analisis_desactualizado),
            "lista_para_compensacion": ready_for_compensation,
            "bloqueos_compensacion": self._build_compensation_blocks_from_context(context),
            "motivo_invalidacion": prep.motivo_invalidacion,
        }

    def _build_legacy_session_payload(self, project, operation, setup, machine) -> dict[str, object]:
        prep = setup.preparacion
        machine_reference_confirmed = bool(machine.home_realizado)
        state = self._derive_state(prep, machine_reference_confirmed)
        gcode_origin = self._build_gcode_origin(operation.analisis)
        ready_for_compensation = not self._build_compensation_blocks(prep, machine_reference_confirmed)
        map_step_state, map_step_detail = self._map_step_status(prep, machine_reference_confirmed)
        validation_step_state, validation_step_detail = self._validation_step_status(prep, machine_reference_confirmed)
        steps = [
            {
                "id": "referencia_maquina",
                "titulo": "Referencia de maquina",
                "estado": "confirmado" if machine_reference_confirmed else "pendiente",
                "confirmado": machine_reference_confirmed,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
                "detalle": "Referencia de maquina confirmada." if machine_reference_confirmed else "Falta confirmar la referencia de maquina.",
            },
            {
                "id": "origen_xy",
                "titulo": "Origen de trabajo X/Y",
                "estado": "confirmado" if prep.origen_trabajo is not None else "pendiente",
                "confirmado": prep.origen_trabajo is not None,
                "fecha": None if prep.origen_trabajo is None or prep.origen_trabajo.confirmado_en is None else prep.origen_trabajo.confirmado_en.isoformat(),
                "detalle": "Origen de trabajo X/Y confirmado." if prep.origen_trabajo is not None else "Pendiente de origen de trabajo X/Y.",
            },
            {
                "id": "referencia_z",
                "titulo": "Referencia Z",
                "estado": "confirmado" if prep.referencia_z is not None else "pendiente",
                "confirmado": prep.referencia_z is not None,
                "fecha": None if prep.referencia_z is None or prep.referencia_z.confirmado_en is None else prep.referencia_z.confirmado_en.isoformat(),
                "detalle": "Referencia Z confirmada." if prep.referencia_z is not None else "Pendiente de referencia Z.",
            },
            {
                "id": "region_sondeable",
                "titulo": "Region sondeable",
                "estado": "confirmado" if prep.region_sondeable_configurada_en is not None else "pendiente",
                "confirmado": prep.region_sondeable_configurada_en is not None,
                "fecha": None if prep.region_sondeable_configurada_en is None else prep.region_sondeable_configurada_en.isoformat(),
                "detalle": "Región sondeable configurada." if prep.region_sondeable_configurada_en is not None else "Configure una región sondeable para definir el dominio interpolable.",
            },
            {
                "id": "mapa",
                "titulo": "Mapa",
                "estado": map_step_state,
                "confirmado": prep.mapa_disponible_en is not None,
                "fecha": None if prep.mapa_disponible_en is None else prep.mapa_disponible_en.isoformat(),
                "detalle": map_step_detail,
            },
            {
                "id": "validacion",
                "titulo": "Validacion",
                "estado": validation_step_state,
                "confirmado": prep.mapa_validado_en is not None,
                "fecha": None if prep.mapa_validado_en is None else prep.mapa_validado_en.isoformat(),
                "detalle": validation_step_detail,
            },
        ]
        return {
            "estado": state,
            "machine_reference": {
                "confirmada": machine_reference_confirmed,
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
            "lista_para_compensacion": ready_for_compensation,
            "bloqueos_compensacion": self._build_compensation_blocks(prep, machine_reference_confirmed),
            "motivo_invalidacion": prep.motivo_invalidacion,
        }

    def _physical_context(self, project_id: str, operation, setup, machine) -> dict[str, Any]:
        physical_map = self._active_physical_map(project_id, operation)
        complete_map = physical_map if physical_map is not None and self._is_complete_measured_map(physical_map) else None
        origin = setup.preparacion.origen_trabajo
        z_reference = setup.preparacion.referencia_z
        region_at = setup.preparacion.region_sondeable_configurada_en
        map_at = setup.preparacion.mapa_disponible_en
        validation_at = setup.preparacion.mapa_validado_en
        machine_reference = machine.home_realizado
        if complete_map is not None:
            machine_reference = machine_reference or self._homed_axes_valid(complete_map.get("homed_axes"))
            origin = origin or self._origin_from_physical_map(complete_map)
            z_reference = z_reference or self._z_reference_from_physical_map(complete_map, operation)
            completed_at = self._parse_datetime(complete_map.get("completed_at")) or self._parse_datetime(complete_map.get("updated_at"))
            region_at = region_at or completed_at
            map_at = map_at or completed_at
            validation = complete_map.get("validation") or {}
            if validation.get("status") == "VALID" and validation.get("sufficient") is True:
                validation_at = validation_at or self._parse_datetime(validation.get("validated_at")) or completed_at
        return {
            "physical_map": complete_map,
            "machine_reference_confirmed": machine_reference,
            "origen_trabajo": origin,
            "referencia_z": z_reference,
            "region_sondeable_configurada_en": region_at,
            "mapa_disponible_en": map_at,
            "mapa_validado_en": validation_at,
        }

    def _active_physical_map(self, project_id: str, operation) -> dict[str, Any] | None:
        if self.physical_map_service is None:
            return None
        try:
            return self.physical_map_service.get_active(project_id, operation.id)
        except Exception:
            return None

    def _is_complete_measured_map(self, physical_map: dict[str, Any]) -> bool:
        if physical_map.get("source") != "MEASURED":
            return False
        if physical_map.get("status") == "MESH_COMPLETE":
            return True
        validation = physical_map.get("validation") or {}
        return physical_map.get("map_ready_state") == "MAP_READY" and validation.get("status") in {"VALID", "INVALID"}

    def _homed_axes_valid(self, homed_axes: object) -> bool:
        return set("xyz").issubset(set(str(homed_axes or "").lower()))

    def _parse_datetime(self, value: object) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _origin_from_physical_map(self, physical_map: dict[str, Any]) -> CoordinateReference:
        timestamp = self._parse_datetime(physical_map.get("completed_at")) or self._parse_datetime(physical_map.get("updated_at")) or utc_now()
        return CoordinateReference(
            x_mm=float(physical_map.get("machine_origin_x", 0.0)),
            y_mm=float(physical_map.get("machine_origin_y", 0.0)),
            z_mm=None,
            confirmado_en=timestamp,
            fuente="MEASURED",
            maquina=physical_map.get("machine_label"),
            homed_axes=physical_map.get("homed_axes"),
            posicion_captura=CapturedPosition(x_mm=float(physical_map.get("machine_origin_x", 0.0)), y_mm=float(physical_map.get("machine_origin_y", 0.0)), z_mm=None),
            sesion=physical_map.get("session_id"),
        )

    def _z_reference_from_physical_map(self, physical_map: dict[str, Any], operation) -> CoordinateReference | None:
        references = physical_map.get("tool_references") or {}
        candidates = []
        key = operation.tool_id or operation.herramienta
        if key and key in references:
            candidates.append(references[key])
        candidates.extend(reference for reference in references.values() if isinstance(reference, dict))
        for reference in candidates:
            if not isinstance(reference, dict) or not reference.get("valid"):
                continue
            if operation.tool_id and reference.get("tool_id") not in {operation.tool_id, None}:
                continue
            timestamp = self._parse_datetime(reference.get("measured_at")) or self._parse_datetime(physical_map.get("completed_at")) or utc_now()
            return CoordinateReference(
                x_mm=float(reference.get("reference_x", physical_map.get("machine_origin_x", 0.0))),
                y_mm=float(reference.get("reference_y", physical_map.get("machine_origin_y", 0.0))),
                z_mm=float(reference.get("reference_z", physical_map.get("reference_z", 0.0))),
                confirmado_en=timestamp,
                fuente=str(reference.get("source") or "MEASURED"),
                maquina=reference.get("machine_label") or physical_map.get("machine_label"),
                homed_axes=reference.get("homed_axes") or physical_map.get("homed_axes"),
                posicion_captura=CapturedPosition(
                    x_mm=float(reference.get("reference_x", physical_map.get("machine_origin_x", 0.0))),
                    y_mm=float(reference.get("reference_y", physical_map.get("machine_origin_y", 0.0))),
                    z_mm=float(reference.get("reference_z", physical_map.get("reference_z", 0.0))),
                ),
                sesion=reference.get("session_id") or physical_map.get("session_id"),
            )
        return None

    def _derive_state_from_values(self, machine_reference_confirmed: bool, origin, z_reference, region_at, map_at, validation_at, preview_at) -> str:
        if not machine_reference_confirmed:
            if origin or z_reference or region_at:
                return PreparationState.REFERENCIA_MAQUINA_PENDIENTE
            return PreparationState.SIN_INICIAR
        if origin is None:
            return PreparationState.ORIGEN_XY_PENDIENTE
        if z_reference is None:
            return PreparationState.REFERENCIA_Z_PENDIENTE
        if region_at is None:
            return PreparationState.REFERENCIA_Z_CONFIRMADA
        if map_at is None:
            return PreparationState.REGION_SONDEABLE_CONFIGURADA
        if validation_at is None:
            return PreparationState.MAPA_DISPONIBLE
        if preview_at is None:
            return PreparationState.MAPA_VALIDADO
        return PreparationState.COMPENSACION_PREVISUALIZADA

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

    def _serialize_reference(self, reference: CoordinateReference | None) -> dict[str, object] | None:
        if reference is None:
            return None
        position = None if reference.posicion_captura is None else {
            "x_mm": reference.posicion_captura.x_mm,
            "y_mm": reference.posicion_captura.y_mm,
            "z_mm": reference.posicion_captura.z_mm,
        }
        return {
            "x_mm": reference.x_mm,
            "y_mm": reference.y_mm,
            "z_mm": reference.z_mm,
            "fecha": None if reference.confirmado_en is None else reference.confirmado_en.isoformat(),
            "fuente": reference.fuente,
            "maquina": reference.maquina,
            "homed_axes": reference.homed_axes,
            "posicion_captura": position,
            "sesion": reference.sesion,
        }

    def _captured_position_from_dict(self, position: dict[str, float]) -> CapturedPosition:
        return CapturedPosition(
            x_mm=float(position["x_mm"]),
            y_mm=float(position["y_mm"]),
            z_mm=None if position.get("z_mm") is None else float(position["z_mm"]),
        )

    def _reject_simulated_overwrite_of_measured(self, reference: CoordinateReference | None, label: str) -> None:
        if reference is not None and reference.fuente == "MEASURED":
            raise ApplicationError(
                f"No se reemplaza una {label} medida con una referencia simulada. Capture una nueva referencia física o elimine la referencia existente de forma explícita."
            )

    def _validate_xy_against_material(self, material_x: float, material_y: float, x_mm: float, y_mm: float, label: str) -> None:
        if x_mm < 0 or y_mm < 0 or x_mm > material_x or y_mm > material_y:
            raise ApplicationError(f"La coordenada de {label} debe quedar dentro del material.")

    def _build_compensation_blocks_from_context(self, context: dict[str, Any]) -> list[str]:
        blocks: list[str] = []
        if not context.get("machine_reference_confirmed"):
            blocks.append("Falta homing válido reportado por Klipper.")
        if context.get("origen_trabajo") is None:
            blocks.append("Falta origen X/Y medido.")
        if context.get("referencia_z") is None:
            blocks.append("Falta referencia Z medida de la herramienta.")
        if context.get("region_sondeable_configurada_en") is None:
            blocks.append("Falta región sondeable de la malla física.")
        physical_map = context.get("physical_map")
        if context.get("mapa_disponible_en") is None or physical_map is None:
            blocks.append("Falta mapa de alturas físico medido y activo.")
        elif not self._is_complete_measured_map(physical_map):
            blocks.append("El mapa físico aún no está completo.")
        if context.get("mapa_validado_en") is None:
            blocks.append("Falta validación de cobertura del mapa físico.")
        return blocks

    def _map_step_status_from_values(self, map_at, machine_reference_confirmed: bool, origin, z_reference, validation_at) -> tuple[str, str]:
        if map_at is None:
            return "pendiente", "Todavía no hay un mapa disponible."
        if not machine_reference_confirmed or origin is None or z_reference is None:
            return "disponible", "Mapa disponible, pendiente de referencias."
        if validation_at is None:
            return "disponible", "Mapa medido activo, pendiente de validación de cobertura."
        return "confirmado", "Mapa medido y activo."

    def _validation_step_status_from_values(self, map_at, validation_at, machine_reference_confirmed: bool, origin, z_reference, invalidation: str | None, physical_map: dict[str, Any] | None) -> tuple[str, str]:
        if validation_at is None:
            if map_at is None:
                return "pendiente", "La validación requiere un mapa disponible."
            validation = (physical_map or {}).get("validation") or {}
            if validation.get("status") == "INVALID":
                return "invalidado", "La cobertura del mapa físico no cubre todas las trayectorias."
            if invalidation:
                return "invalidado", invalidation
            return "pendiente", "La validación del mapa sigue pendiente."
        if not machine_reference_confirmed or origin is None or z_reference is None:
            return "disponible", "Validación registrada, pero la compensación sigue bloqueada por referencias pendientes."
        return "confirmado", "Cobertura validada."

    def _build_compensation_blocks(self, preparation, machine_reference_confirmed: bool) -> list[str]:
        blocks: list[str] = []
        if not machine_reference_confirmed:
            blocks.append("Falta confirmar la referencia de maquina.")
        if preparation.origen_trabajo is None:
            blocks.append("Falta confirmar el origen de trabajo X/Y.")
        if preparation.referencia_z is None:
            blocks.append("Falta confirmar la referencia Z.")
        if preparation.region_sondeable_configurada_en is None:
            blocks.append("Falta configurar la region sondeable.")
        if preparation.mapa_disponible_en is None:
            blocks.append("Falta disponer de un mapa de alturas.")
        if preparation.mapa_validado_en is None:
            blocks.append("Falta validar el mapa de alturas.")
        return blocks

    def _map_step_status(self, preparation, machine_reference_confirmed: bool) -> tuple[str, str]:
        if preparation.mapa_disponible_en is None:
            return "pendiente", "Todavía no hay un mapa disponible."
        if not machine_reference_confirmed or preparation.origen_trabajo is None or preparation.referencia_z is None:
            return "disponible", "Mapa disponible, pendiente de referencias."
        if preparation.mapa_validado_en is None:
            return "disponible", "Mapa disponible, pendiente de validación."
        return "confirmado", "Mapa disponible y validado."

    def _validation_step_status(self, preparation, machine_reference_confirmed: bool) -> tuple[str, str]:
        if preparation.mapa_validado_en is None:
            if preparation.mapa_disponible_en is None:
                return "pendiente", "La validación requiere un mapa disponible."
            if preparation.motivo_invalidacion:
                return "invalidado", preparation.motivo_invalidacion
            return "pendiente", "La validación del mapa sigue pendiente."
        if not machine_reference_confirmed or preparation.origen_trabajo is None or preparation.referencia_z is None:
            return "disponible", "Validación registrada, pero la compensación sigue bloqueada por referencias pendientes."
        return "confirmado", "Preparación lista para compensación matemática."

    def _build_invalidation_reason(self, preparation, message: str) -> str | None:
        if (
            preparation.mapa_disponible_en is not None
            or preparation.mapa_validado_en is not None
            or preparation.compensacion_previsualizada_en is not None
        ):
            return message
        return preparation.motivo_invalidacion

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
