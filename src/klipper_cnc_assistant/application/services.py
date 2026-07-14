from __future__ import annotations

import platform
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePath
from uuid import uuid4

from klipper_cnc_assistant import __version__
from klipper_cnc_assistant.domain import (
    AgujeroAlineacion,
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


ALLOWED_GCODE_EXTENSIONS = {".nc", ".gcode", ".tap"}
DEFAULT_MAX_GCODE_FILE_BYTES = 5 * 1024 * 1024


class ProjectService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        *,
        max_gcode_file_bytes: int = DEFAULT_MAX_GCODE_FILE_BYTES,
    ) -> None:
        self.repository = repository
        self.max_gcode_file_bytes = max_gcode_file_bytes

    def list_projects(self) -> list[ProyectoPCB]:
        return sorted(
            self.repository.list_projects(),
            key=lambda project: project.actualizado_en,
            reverse=True,
        )

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
            configuracion_alineacion=self._build_alignment_configuration(
                doble_cara=doble_cara,
                eje_volteo=eje_volteo,
                agujeros_alineacion=agujeros_alineacion,
            ),
        )
        self.repository.save_project(project)
        return project

    def update_project(
        self,
        *,
        project_id: str,
        nombre: str,
        ancho_mm: float,
        alto_mm: float,
        espesor_mm: float | None = None,
        doble_cara: bool = False,
        eje_volteo: str | None = None,
        agujeros_alineacion: list[dict] | None = None,
    ) -> ProyectoPCB:
        project = self._load_project(project_id)
        updated = project.update_metadata(
            nombre=nombre,
            material=MaterialBruto(
                ancho_mm=ancho_mm,
                alto_mm=alto_mm,
                espesor_mm=espesor_mm,
            ),
            configuracion_alineacion=self._build_alignment_configuration(
                doble_cara=doble_cara,
                eje_volteo=eje_volteo,
                agujeros_alineacion=agujeros_alineacion,
            ),
        )
        self.repository.save_project(updated)
        return updated

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

    def remove_operation_gcode(
        self,
        *,
        project_id: str,
        operation_id: str,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        updated_operation = operation.without_gcode()
        updated_project = project.replace_operation(updated_operation)
        self.repository.save_project(updated_project)
        return updated_project.get_operation(operation_id)

    def upload_operation_gcode(
        self,
        *,
        project_id: str,
        operation_id: str,
        filename: str,
        content: str,
    ) -> OperacionPCB:
        return self._store_operation_gcode(
            project_id=project_id,
            operation_id=operation_id,
            filename=filename,
            content=content,
        )

    def upload_operation_gcode_bytes(
        self,
        *,
        project_id: str,
        operation_id: str,
        filename: str,
        content_bytes: bytes,
    ) -> OperacionPCB:
        content = self._decode_uploaded_content(content_bytes)
        return self._store_operation_gcode(
            project_id=project_id,
            operation_id=operation_id,
            filename=filename,
            content=content,
        )

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
        updated_operation = operation.with_analysis(analysis)
        updated_project = project.replace_operation(updated_operation)
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
        content = file_path.read_text(encoding="utf-8")
        return analyze_gcode_text(content)

    def storage_available(self) -> bool:
        return self.repository.storage_available()

    def _store_operation_gcode(
        self,
        *,
        project_id: str,
        operation_id: str,
        filename: str,
        content: str,
    ) -> OperacionPCB:
        validated_name = self._validate_upload_filename(filename)
        encoded = content.encode("utf-8")
        self._validate_upload_size(len(encoded))
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        relative_path, sha256, size_bytes = self.repository.store_original_text(
            project_id,
            filename=validated_name,
            content=content,
        )
        updated_operation = operation.with_gcode(
            archivo_gcode=relative_path,
            nombre_archivo_original=validated_name,
            tamano_archivo_bytes=size_bytes,
            sha256=sha256,
        )
        updated_project = project.replace_operation(updated_operation)
        self.repository.save_project(updated_project)
        return updated_project.get_operation(operation_id)

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

    def _build_alignment_configuration(
        self,
        *,
        doble_cara: bool,
        eje_volteo: str | None,
        agujeros_alineacion: list[dict] | None,
    ) -> ConfiguracionAlineacion:
        return ConfiguracionAlineacion(
            doble_cara=doble_cara,
            eje_volteo=FlipAxis(eje_volteo) if eje_volteo else None,
            agujeros_alineacion=tuple(
                self._build_alignment_holes(agujeros_alineacion or [])
            ),
        )

    def _build_alignment_holes(
        self,
        holes: list[dict],
    ) -> list[AgujeroAlineacion]:
        return [AgujeroAlineacion(**hole) for hole in holes]

    def _validate_upload_filename(self, filename: str) -> str:
        candidate = filename.strip()
        if not candidate:
            raise ApplicationError(
                "El archivo G-code debe tener un nombre valido."
            )
        if candidate != PurePath(candidate).name:
            raise ApplicationError(
                "El nombre del archivo no puede incluir rutas."
            )
        extension = Path(candidate).suffix.lower()
        if extension not in ALLOWED_GCODE_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_GCODE_EXTENSIONS))
            raise ApplicationError(
                f"Extension no permitida. Use uno de: {allowed}."
            )
        return candidate

    def _validate_upload_size(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            raise ApplicationError(
                "El archivo G-code no puede estar vacio."
            )
        if size_bytes > self.max_gcode_file_bytes:
            raise ApplicationError(
                "El archivo G-code excede el tamano maximo permitido."
            )

    def _decode_uploaded_content(self, content_bytes: bytes) -> str:
        self._validate_upload_size(len(content_bytes))
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ApplicationError(
                "El archivo G-code debe estar codificado en UTF-8."
            ) from error


class MachineSessionService:
    machine_mode = "simulado"

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


class SystemStatusService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        machine_session_service: MachineSessionService,
    ) -> None:
        self.repository = repository
        self.machine_session_service = machine_session_service

    def get_health(self) -> dict[str, str]:
        return {
            "estado": "ok",
            "version": __version__,
            "modo_maquina": self.machine_session_service.machine_mode,
            "almacenamiento": self._storage_label(),
        }

    def get_system_info(self) -> dict[str, str | bool]:
        now = datetime.now(timezone.utc)
        return {
            "estado": "ok",
            "version_aplicacion": __version__,
            "version_python": platform.python_version(),
            "almacenamiento_disponible": self.repository.storage_available(),
            "estado_api": "operativa",
            "modo_maquina": self.machine_session_service.machine_mode,
            "hora_servidor": now.isoformat(),
        }

    def _storage_label(self) -> str:
        return "disponible" if self.repository.storage_available() else "no disponible"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"
