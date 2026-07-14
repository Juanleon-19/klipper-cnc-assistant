from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from klipper_cnc_assistant.domain import CoordinateReference, PreparationState, ProjectValidationError
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
    ) -> None:
        self.repository = repository
        self.height_map_service = height_map_service
        self.machine_session_service = machine_session_service

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
            posicion_captura=dict(position),
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
            posicion_captura=dict(position),
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
        blocks = self._build_compensation_blocks(setup.preparacion, machine.home_realizado)
        if blocks:
            raise ApplicationError("La previsualizacion sigue bloqueada. " + " ".join(blocks))
        if operation.analisis is None:
            raise ApplicationError("La operacion requiere un analisis G-code antes de previsualizar la compensacion.")
        height_map = self.height_map_service.get_map(project_id, operation_id)
        preview = build_compensation_preview(
            analysis=operation.analisis,
            height_map=height_map,
            reference_z_mm=setup.preparacion.referencia_z.z_mm if setup.preparacion.referencia_z and setup.preparacion.referencia_z.z_mm is not None else 0.0,
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

    def _build_session_payload(self, project, operation, setup, machine) -> dict[str, object]:
        prep = setup.preparacion
        state = self._derive_state(setup.preparacion, machine.home_realizado)
        gcode_origin = self._build_gcode_origin(operation.analisis)
        ready_for_compensation = not self._build_compensation_blocks(prep, machine.home_realizado)
        map_step_state, map_step_detail = self._map_step_status(prep, machine.home_realizado)
        validation_step_state, validation_step_detail = self._validation_step_status(prep, machine.home_realizado)
        steps = [
            {
                "id": "referencia_maquina",
                "titulo": "Referencia de maquina",
                "estado": "confirmado" if machine.home_realizado else "pendiente",
                "confirmado": machine.home_realizado,
                "fecha": None if machine.referencia_maquina_confirmada_en is None else machine.referencia_maquina_confirmada_en.isoformat(),
                "detalle": "Pertenece a la sesión general de máquina." if machine.home_realizado else "Falta confirmar la referencia de máquina en simulación.",
            },
            {
                "id": "origen_xy",
                "titulo": "Origen de trabajo X/Y",
                "estado": "confirmado" if prep.origen_trabajo is not None else "pendiente",
                "confirmado": prep.origen_trabajo is not None,
                "fecha": None if prep.origen_trabajo is None or prep.origen_trabajo.confirmado_en is None else prep.origen_trabajo.confirmado_en.isoformat(),
                "detalle": "Define dónde queda X0 Y0 del G-code respecto al montaje." if prep.origen_trabajo is not None else "Pendiente de confirmación en simulación.",
            },
            {
                "id": "referencia_z",
                "titulo": "Referencia Z",
                "estado": "confirmado" if prep.referencia_z is not None else "pendiente",
                "confirmado": prep.referencia_z is not None,
                "fecha": None if prep.referencia_z is None or prep.referencia_z.confirmado_en is None else prep.referencia_z.confirmado_en.isoformat(),
                "detalle": "Referencia vertical del montaje." if prep.referencia_z is not None else "Pendiente de confirmación en simulación.",
            },
            {
                "id": "region_sondeable",
                "titulo": "Region sondeable",
                "estado": "confirmado" if prep.region_sondeable_configurada_en is not None else "pendiente",
                "confirmado": prep.region_sondeable_configurada_en is not None,
                "fecha": None if prep.region_sondeable_configurada_en is None else prep.region_sondeable_configurada_en.isoformat(),
                "detalle": "Dominio medido del mapa." if prep.region_sondeable_configurada_en is not None else "Configure una región sondeable para definir el dominio interpolable.",
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
            "lista_para_compensacion": ready_for_compensation,
            "bloqueos_compensacion": self._build_compensation_blocks(prep, machine.home_realizado),
            "motivo_invalidacion": prep.motivo_invalidacion,
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
            "fuente": reference.fuente,
            "maquina": reference.maquina,
            "homed_axes": reference.homed_axes,
            "posicion_captura": reference.posicion_captura,
            "sesion": reference.sesion,
        }

    def _reject_simulated_overwrite_of_measured(self, reference: CoordinateReference | None, label: str) -> None:
        if reference is not None and reference.fuente == "MEASURED":
            raise ApplicationError(
                f"No se reemplaza una {label} medida con una referencia simulada. Capture una nueva referencia física o elimine la referencia existente de forma explícita."
            )

    def _validate_xy_against_material(self, material_x: float, material_y: float, x_mm: float, y_mm: float, label: str) -> None:
        if x_mm < 0 or y_mm < 0 or x_mm > material_x or y_mm > material_y:
            raise ApplicationError(f"La coordenada de {label} debe quedar dentro del material.")

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
