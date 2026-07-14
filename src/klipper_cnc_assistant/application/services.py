from __future__ import annotations

import os
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
    MontajePCB,
    OperationType,
    OperacionPCB,
    ProyectoPCB,
    ProjectValidationError,
    PROJECT_SCHEMA_VERSION,
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

    def add_setup(
        self,
        *,
        project_id: str,
        nombre: str,
    ) -> MontajePCB:
        project = self._load_project(project_id)
        setup = MontajePCB(
            id=_new_id("setup"),
            nombre=nombre,
            orden=len(project.montajes),
        )
        updated = project.add_setup(setup)
        self.repository.save_project(updated)
        return updated.get_setup(setup.id)

    def update_setup(
        self,
        *,
        project_id: str,
        setup_id: str,
        nombre: str,
    ) -> MontajePCB:
        project = self._load_project(project_id)
        updated_setup = replace(project.get_setup(setup_id), nombre=nombre)
        updated = project.replace_setup(updated_setup)
        self.repository.save_project(updated)
        return updated.get_setup(setup_id)

    def add_operation(
        self,
        *,
        project_id: str,
        nombre: str,
        tipo: str,
        cara: str | None = None,
        orden: int | None = None,
        setup_id: str | None = None,
        tool_id: str | None = None,
        herramienta: str | None = None,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        target_setup_id = setup_id or project.montajes[0].id
        project.get_setup(target_setup_id)
        operation_type = OperationType(tipo)
        operation_face = BoardFace(
            cara
            or (
                BoardFace.INFERIOR
                if operation_type == OperationType.FRESADO_INFERIOR
                else BoardFace.SUPERIOR
            )
        )
        setup_operations = project.operations_for_setup(target_setup_id)
        next_order = len(setup_operations) if orden is None else orden
        operation = OperacionPCB(
            id=_new_id("op"),
            nombre=nombre,
            tipo=operation_type,
            cara=operation_face,
            orden=next_order,
            setup_id=target_setup_id,
            tool_id=tool_id,
            herramienta=herramienta,
        )
        updated = project.add_operation(operation)
        self.repository.save_project(updated)
        return updated.get_operation(operation.id)

    def update_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        nombre: str,
        tool_id: str | None = None,
        herramienta: str | None = None,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        updated_operation = replace(
            operation,
            nombre=nombre,
            tool_id=tool_id,
            herramienta=herramienta,
        )
        updated = project.replace_operation(updated_operation)
        self.repository.save_project(updated)
        return updated.get_operation(operation_id)

    def duplicate_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
    ) -> OperacionPCB:
        project = self._load_project(project_id)
        source = project.get_operation(operation_id)
        return self.add_operation(
            project_id=project_id,
            nombre=f"{source.nombre} (copia)",
            tipo=source.tipo,
            cara=source.cara,
            setup_id=source.setup_id,
            tool_id=source.tool_id,
            herramienta=source.herramienta,
        )

    def move_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        direction: str,
    ) -> OperacionPCB:
        if direction not in {"up", "down"}:
            raise ApplicationError("La direccion debe ser up o down.")
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        ordered = list(project.operations_for_setup(operation.setup_id))
        index = next(index for index, item in enumerate(ordered) if item.id == operation_id)
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(ordered):
            return operation
        ordered[index], ordered[target] = ordered[target], ordered[index]
        reordered = {item.id: replace(item, orden=order) for order, item in enumerate(ordered)}
        updated = replace(
            project,
            operaciones=tuple(
                reordered.get(item.id, item)
                for item in project.operaciones
            ),
            actualizado_en=datetime.now(timezone.utc),
        )
        self.repository.save_project(updated)
        return updated.get_operation(operation_id)

    def delete_operation(
        self,
        project_id: str,
        operation_id: str,
    ) -> None:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        updated = project.remove_operation(operation_id)
        ordered = updated.operations_for_setup(operation.setup_id)
        reordered = {item.id: replace(item, orden=order) for order, item in enumerate(ordered)}
        normalized = replace(
            updated,
            operaciones=tuple(reordered.get(item.id, item) for item in updated.operaciones),
            actualizado_en=datetime.now(timezone.utc),
        )
        self.repository.save_project(normalized)

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

    def __init__(self) -> None:
        self.referencia_maquina_confirmada_en: datetime | None = None

    def confirm_reference_in_simulation(self) -> MachineSessionStatus:
        if self.referencia_maquina_confirmada_en is None:
            self.referencia_maquina_confirmada_en = datetime.now(timezone.utc)
        return self.get_status()

    def get_status(self) -> MachineSessionStatus:
        home_realizado = self.referencia_maquina_confirmada_en is not None
        return MachineSessionStatus(
            estado="simulada_lista_para_preparacion",
            home_realizado=home_realizado,
            referencia_maquina_confirmada_en=self.referencia_maquina_confirmada_en,
            z_en_altura_segura=True,
            herramienta_en_centro_cama=True,
            material_montado=False,
            origen_xy_definido=False,
            cero_z_capturado=False,
            operaciones_permitidas=(
                "crear proyecto",
                "cargar gcode",
                "analizar gcode",
                "confirmar referencias en simulacion",
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
            "backend_version": __version__,
            "frontend_build": os.getenv("KCA_FRONTEND_BUILD", "dev"),
            "git_commit": os.getenv("KCA_GIT_COMMIT"),
            "schema_version": PROJECT_SCHEMA_VERSION,
        }

    def _storage_label(self) -> str:
        return "disponible" if self.repository.storage_available() else "no disponible"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"
