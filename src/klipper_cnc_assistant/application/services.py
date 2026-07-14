from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from klipper_cnc_assistant.domain import (
    BoardFace,
    ConfiguracionAlineacion,
    FlipAxis,
    MachineSessionStatus,
    MaterialBruto,
    OperationType,
    OperacionPCB,
    ProyectoPCB,
    ProjectValidationError,
)
from klipper_cnc_assistant.gcode import analyze_gcode_text
from klipper_cnc_assistant.storage import JsonProjectRepository

from .errors import ApplicationError, NotFoundError


class ProjectService:
    def __init__(
        self,
        repository: JsonProjectRepository,
    ) -> None:
        self.repository = repository

    def list_projects(self) -> list[ProyectoPCB]:
        return self.repository.list_projects()

    def create_project(
        self,
        *,
        nombre: str,
        ancho_mm: float,
        alto_mm: float,
        espesor_mm: float | None = None,
        doble_cara: bool = False,
        eje_volteo: str | None = None,
        agujeros_alineacion: list[dict] | None = None,
    ) -> ProyectoPCB:
        project = ProyectoPCB(
            id=_new_id("proj"),
            nombre=nombre,
            material=MaterialBruto(
                ancho_mm=ancho_mm,
                alto_mm=alto_mm,
                espesor_mm=espesor_mm,
            ),
            configuracion_alineacion=ConfiguracionAlineacion(
                doble_cara=doble_cara,
                eje_volteo=(
                    FlipAxis(eje_volteo)
                    if eje_volteo
                    else None
                ),
                agujeros_alineacion=tuple(
                    [] if agujeros_alineacion is None else []
                ),
            ),
        )
        if agujeros_alineacion:
            project = replace(
                project,
                configuracion_alineacion=ConfiguracionAlineacion(
                    doble_cara=doble_cara,
                    eje_volteo=(
                        FlipAxis(eje_volteo)
                        if eje_volteo
                        else None
                    ),
                    agujeros_alineacion=tuple(
                        self._build_alignment_holes(
                            agujeros_alineacion
                        )
                    ),
                ),
            )
        self.repository.save_project(project)
        return project

    def get_project(
        self,
        project_id: str,
    ) -> ProyectoPCB:
        return self._load_project(project_id)

    def add_operation(
        self,
        *,
        project_id: str,
        nombre: str,
        tipo: str,
        cara: str,
        orden: int,
        herramienta: str | None = None,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        operation = OperacionPCB(
            id=_new_id("op"),
            nombre=nombre,
            tipo=OperationType(tipo),
            cara=BoardFace(cara),
            orden=orden,
            herramienta=herramienta,
        )
        updated = project.add_operation(operation)
        self.repository.save_project(updated)
        return updated.get_operation(operation.id)

    def delete_operation(
        self,
        project_id: str,
        operation_id: str,
    ) -> None:
        project = self._load_project(project_id)
        updated = project.remove_operation(operation_id)
        self.repository.save_project(updated)

    def upload_operation_gcode(
        self,
        *,
        project_id: str,
        operation_id: str,
        filename: str,
        content: str,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        relative_path, sha256 = (
            self.repository.store_original_text(
                project_id,
                filename=filename,
                content=content,
            )
        )
        updated_operation = operation.with_gcode(
            archivo_gcode=relative_path,
            sha256=sha256,
        )
        updated_project = project.replace_operation(
            updated_operation
        )
        self.repository.save_project(updated_project)
        return updated_project.get_operation(operation_id)

    def analyze_operation(
        self,
        project_id: str,
        operation_id: str,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        if not operation.archivo_gcode:
            raise ApplicationError(
                "La operacion no tiene un archivo G-code cargado."
            )
        content = self.repository.read_project_file(
            project_id,
            operation.archivo_gcode,
        )
        analysis = analyze_gcode_text(
            content,
            material=project.material,
        )
        updated_operation = operation.with_analysis(
            analysis
        )
        updated_project = project.replace_operation(
            updated_operation
        )
        self.repository.save_project(updated_project)
        return updated_project.get_operation(operation_id)

    def get_operation_analysis(
        self,
        project_id: str,
        operation_id: str,
    ):
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        if operation.analisis is None:
            raise ApplicationError(
                "La operacion todavia no tiene analisis."
            )
        return operation.analisis

    def check_gcode_file(
        self,
        file_path: Path,
    ):
        content = file_path.read_text(
            encoding="utf-8"
        )
        return analyze_gcode_text(content)

    def _load_project(
        self,
        project_id: str,
    ) -> ProyectoPCB:
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error

    def _build_alignment_holes(
        self,
        holes: list[dict],
    ):
        from klipper_cnc_assistant.domain import (
            AgujeroAlineacion,
        )

        return [
            AgujeroAlineacion(**hole)
            for hole in holes
        ]


class MachineSessionService:
    def get_status(self) -> MachineSessionStatus:
        return MachineSessionStatus(
            estado="simulada_lista_para_preparacion",
            home_realizado=True,
            z_en_altura_segura=True,
            herramienta_en_centro_cama=True,
            material_montado=False,
            origen_xy_definido=False,
            cero_z_capturado=False,
            operaciones_permitidas=(
                "crear proyecto",
                "cargar gcode",
                "analizar gcode",
            ),
            z_puede_bajar_durante=(
                "captura del cero",
                "sondeo",
                "mecanizado",
                "movimiento manual autorizado",
            ),
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"
