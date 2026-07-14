from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from klipper_cnc_assistant.domain import (
    AgujeroAlineacion,
    AnalysisIssue,
    BoardFace,
    Bounds3D,
    ConfiguracionAlineacion,
    CoordinateReference,
    FlipAxis,
    IssueSeverity,
    MaterialBruto,
    MaterialOverflow,
    MontajePCB,
    OperationAnalysis,
    OperationPreparation,
    OperationStatus,
    OperationType,
    OperacionPCB,
    PreviewPoint,
    PreviewSegment,
    ProyectoPCB,
    PROJECT_SCHEMA_VERSION,
    ProjectValidationError,
)
from klipper_cnc_assistant.gcode import CURRENT_ANALYSIS_VERSION


def _slugify(value: str) -> str:
    slug = re.sub(
        r"[^a-zA-Z0-9._-]+",
        "-",
        value.strip(),
    ).strip("-")
    return slug or "archivo"


class JsonProjectRepository:
    def __init__(
        self,
        base_dir: Path,
    ) -> None:
        self.base_dir = base_dir
        self.projects_dir = self.base_dir / "projects"
        self.projects_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def list_projects(self) -> list[ProyectoPCB]:
        projects: list[ProyectoPCB] = []
        for project_file in sorted(self.projects_dir.glob("*/project.json")):
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            project = self._deserialize_project(payload)
            if self._needs_project_migration(payload):
                self.save_project(project)
            projects.append(project)
        return projects

    def save_project(
        self,
        project: ProyectoPCB,
    ) -> ProyectoPCB:
        project_dir = self.project_dir(project.id)
        self._ensure_project_layout(project_dir)
        payload = self._serialize_project(project)
        (project_dir / "project.json").write_text(
            json.dumps(
                payload,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return project

    def load_project(
        self,
        project_id: str,
    ) -> ProyectoPCB:
        project_file = self.project_dir(project_id) / "project.json"
        if not project_file.exists():
            raise FileNotFoundError(
                f"El proyecto '{project_id}' no existe."
            )
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        project = self._deserialize_project(payload)
        if self._needs_project_migration(payload):
            self.save_project(project)
        return project

    def _needs_project_migration(self, payload: dict) -> bool:
        return (
            payload.get("version_esquema") != PROJECT_SCHEMA_VERSION
            or not payload.get("montajes")
            or any("setup_id" not in item for item in payload.get("operaciones", []))
        )

    def project_dir(
        self,
        project_id: str,
    ) -> Path:
        return self.projects_dir / project_id

    def storage_available(self) -> bool:
        try:
            self._ensure_project_layout(self.projects_dir)
        except OSError:
            return False
        probe = self.base_dir / ".storage_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            return False
        return True

    def store_original_text(
        self,
        project_id: str,
        *,
        filename: str,
        content: str,
    ) -> tuple[str, str, int]:
        encoded = content.encode("utf-8")
        sha256 = hashlib.sha256(encoded).hexdigest()
        project_dir = self.project_dir(project_id)
        self._ensure_project_layout(project_dir)

        safe_name = _slugify(filename)
        relative_path = Path("originals") / f"{sha256}_{safe_name}"
        absolute_path = project_dir / relative_path

        if absolute_path.exists():
            existing_sha = hashlib.sha256(absolute_path.read_bytes()).hexdigest()
            if existing_sha != sha256:
                raise RuntimeError(
                    "Se detecto un conflicto al preservar el archivo original."
                )
        else:
            absolute_path.write_bytes(encoded)

        return relative_path.as_posix(), sha256, len(encoded)

    def read_project_file(
        self,
        project_id: str,
        relative_path: str,
    ) -> str:
        target = self._resolve_project_file(project_id, relative_path)
        return target.read_text(encoding="utf-8")

    def save_height_map_payload(
        self,
        project_id: str,
        operation_id: str,
        payload: dict,
    ) -> dict:
        target = self._height_map_file(project_id, operation_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                payload,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return payload

    def load_height_map_payload(
        self,
        project_id: str,
        operation_id: str,
    ) -> dict:
        target = self._height_map_file(project_id, operation_id)
        if not target.exists():
            target = self._shared_height_map_file(project_id, operation_id)
        if target is None or not target.exists():
            raise FileNotFoundError(
                f"El mapa de alturas para la operacion {operation_id} no existe."
            )
        return json.loads(target.read_text(encoding="utf-8"))

    def delete_height_map(
        self,
        project_id: str,
        operation_id: str,
    ) -> None:
        target = self._height_map_file(project_id, operation_id)
        if not target.exists():
            raise FileNotFoundError(
                f"El mapa de alturas para la operacion '{operation_id}' no existe."
            )
        target.unlink()

    def _resolve_project_file(
        self,
        project_id: str,
        relative_path: str,
    ) -> Path:
        project_dir = self.project_dir(project_id)
        target = project_dir / relative_path
        resolved = target.resolve()
        project_root = project_dir.resolve()
        if resolved != project_root and project_root not in resolved.parents:
            raise RuntimeError(
                "La ruta solicitada sale del directorio del proyecto."
            )
        return target

    def _ensure_project_layout(
        self,
        project_dir: Path,
    ) -> None:
        for relative in ("", "originals", "maps", "generated", "reports"):
            (project_dir / relative).mkdir(
                parents=True,
                exist_ok=True,
            )

    def _serialize_project(
        self,
        project: ProyectoPCB,
    ) -> dict:
        return {
            "id": project.id,
            "nombre": project.nombre,
            "material": {
                "ancho_mm": project.material.ancho_mm,
                "alto_mm": project.material.alto_mm,
                "espesor_mm": project.material.espesor_mm,
            },
            "configuracion_alineacion": {
                "doble_cara": project.configuracion_alineacion.doble_cara,
                "eje_volteo": project.configuracion_alineacion.eje_volteo,
                "agujeros_alineacion": [
                    {
                        "x_mm": hole.x_mm,
                        "y_mm": hole.y_mm,
                        "diametro_mm": hole.diametro_mm,
                    }
                    for hole in project.configuracion_alineacion.agujeros_alineacion
                ],
            },
            "montajes": [self._serialize_setup(setup) for setup in project.montajes],
            "operaciones": [self._serialize_operation(operation) for operation in project.operaciones],
            "creado_en": project.creado_en.isoformat(),
            "actualizado_en": project.actualizado_en.isoformat(),
            "version_esquema": project.version_esquema,
        }

    def _serialize_setup(self, setup: MontajePCB) -> dict:
        return {
            "id": setup.id,
            "nombre": setup.nombre,
            "orden": setup.orden,
            "preparacion": self._serialize_preparation(setup.preparacion),
        }

    def _serialize_operation(
        self,
        operation: OperacionPCB,
    ) -> dict:
        return {
            "id": operation.id,
            "nombre": operation.nombre,
            "tipo": operation.tipo,
            "cara": operation.cara,
            "orden": operation.orden,
            "setup_id": operation.setup_id,
            "archivo_gcode": operation.archivo_gcode,
            "nombre_archivo_original": operation.nombre_archivo_original,
            "tamano_archivo_bytes": operation.tamano_archivo_bytes,
            "sha256": operation.sha256,
            "tool_id": operation.tool_id,
            "herramienta": operation.herramienta,
            "analisis": self._serialize_analysis(operation.analisis),
            "estado": operation.estado,
        }

    def _serialize_analysis(
        self,
        analysis: OperationAnalysis | None,
    ) -> dict | None:
        if analysis is None:
            return None
        return {
            "analysis_version": analysis.analysis_version,
            "current_analysis_version": analysis.current_analysis_version,
            "limites": None if analysis.limites is None else {
                "min_x_mm": analysis.limites.min_x_mm,
                "max_x_mm": analysis.limites.max_x_mm,
                "min_y_mm": analysis.limites.min_y_mm,
                "max_y_mm": analysis.limites.max_y_mm,
                "min_z_mm": analysis.limites.min_z_mm,
                "max_z_mm": analysis.limites.max_z_mm,
            },
            "avances_mm_min": list(analysis.avances_mm_min),
            "profundidad_min_mm": analysis.profundidad_min_mm,
            "profundidad_max_mm": analysis.profundidad_max_mm,
            "cantidad_movimientos": analysis.cantidad_movimientos,
            "comandos_desconocidos": list(analysis.comandos_desconocidos),
            "comandos_no_compatibles": list(analysis.comandos_no_compatibles),
            "acciones_husillo": list(analysis.acciones_husillo),
            "cambios_herramienta": list(analysis.cambios_herramienta),
            "unidades_detectadas": list(analysis.unidades_detectadas),
            "modos_posicionamiento": list(analysis.modos_posicionamiento),
            "incidencias": [asdict(issue) for issue in analysis.incidencias],
            "analisis_incompleto": analysis.analisis_incompleto,
            "cabe_en_material": analysis.cabe_en_material,
            "mensaje_material": analysis.mensaje_material,
            "segmentos_lineales": [self._serialize_segment(segment) for segment in analysis.segmentos_lineales],
            "segmentos_vista_previa": [self._serialize_segment(segment) for segment in analysis.segmentos_vista_previa],
            "desbordes_material": [asdict(item) for item in analysis.desbordes_material],
            "tolerancia_arco_mm": analysis.tolerancia_arco_mm,
        }

    def _serialize_preparation(self, preparation: OperationPreparation) -> dict:
        return {
            "origen_trabajo": self._serialize_reference(preparation.origen_trabajo),
            "referencia_z": self._serialize_reference(preparation.referencia_z),
            "region_sondeable_configurada_en": None if preparation.region_sondeable_configurada_en is None else preparation.region_sondeable_configurada_en.isoformat(),
            "mapa_disponible_en": None if preparation.mapa_disponible_en is None else preparation.mapa_disponible_en.isoformat(),
            "mapa_validado_en": None if preparation.mapa_validado_en is None else preparation.mapa_validado_en.isoformat(),
            "compensacion_previsualizada_en": None if preparation.compensacion_previsualizada_en is None else preparation.compensacion_previsualizada_en.isoformat(),
            "motivo_invalidacion": preparation.motivo_invalidacion,
        }

    def _serialize_reference(self, reference: CoordinateReference | None) -> dict | None:
        if reference is None:
            return None
        return {
            "x_mm": reference.x_mm,
            "y_mm": reference.y_mm,
            "z_mm": reference.z_mm,
            "confirmado_en": None if reference.confirmado_en is None else reference.confirmado_en.isoformat(),
            "fuente": reference.fuente,
            "maquina": reference.maquina,
            "homed_axes": reference.homed_axes,
            "posicion_captura": reference.posicion_captura,
            "sesion": reference.sesion,
        }

    def _serialize_segment(self, segment: PreviewSegment) -> dict:
        return {
            "tipo": segment.tipo,
            "tipo_movimiento": segment.tipo_movimiento,
            "numero_linea": segment.numero_linea,
            "inicio_x_mm": segment.inicio_x_mm,
            "inicio_y_mm": segment.inicio_y_mm,
            "fin_x_mm": segment.fin_x_mm,
            "fin_y_mm": segment.fin_y_mm,
            "z_mm": segment.z_mm,
            "avance_mm_min": segment.avance_mm_min,
            "distancia_mm": segment.distancia_mm,
            "advertencias": list(segment.advertencias),
            "puntos": [asdict(point) for point in segment.puntos],
        }

    def _deserialize_project(
        self,
        payload: dict,
    ) -> ProyectoPCB:
        alignment_data = payload.get("configuracion_alineacion", {})
        operation_payloads = payload.get("operaciones", [])
        setup_payloads = payload.get("montajes", [])
        if setup_payloads:
            setups = tuple(
                MontajePCB(
                    id=item["id"],
                    nombre=item["nombre"],
                    orden=item["orden"],
                    preparacion=self._deserialize_preparation(item.get("preparacion")),
                )
                for item in setup_payloads
            )
        else:
            legacy_preparations = [
                self._deserialize_preparation(item.get("preparacion"))
                for item in operation_payloads
            ]
            preparation = max(
                legacy_preparations,
                key=self._preparation_score,
                default=OperationPreparation(),
            )
            setups = (
                MontajePCB(
                    id="setup-main",
                    nombre="Montaje principal",
                    orden=0,
                    preparacion=preparation,
                ),
            )
        default_setup_id = setups[0].id
        return ProyectoPCB(
            id=payload["id"],
            nombre=payload["nombre"],
            material=MaterialBruto(**payload["material"]),
            montajes=setups,
            operaciones=tuple(
                self._deserialize_operation(item, default_setup_id=default_setup_id)
                for item in operation_payloads
            ),
            creado_en=datetime.fromisoformat(payload["creado_en"]),
            actualizado_en=datetime.fromisoformat(payload["actualizado_en"]),
            version_esquema=PROJECT_SCHEMA_VERSION,
            configuracion_alineacion=ConfiguracionAlineacion(
                doble_cara=alignment_data.get("doble_cara", False),
                eje_volteo=(
                    FlipAxis(alignment_data["eje_volteo"])
                    if alignment_data.get("eje_volteo")
                    else None
                ),
                agujeros_alineacion=tuple(
                    AgujeroAlineacion(**hole)
                    for hole in alignment_data.get("agujeros_alineacion", [])
                ),
            ),
        )

    def _preparation_score(self, preparation: OperationPreparation) -> int:
        return sum(
            value is not None
            for value in (
                preparation.origen_trabajo,
                preparation.referencia_z,
                preparation.region_sondeable_configurada_en,
                preparation.mapa_disponible_en,
                preparation.mapa_validado_en,
                preparation.compensacion_previsualizada_en,
            )
        )

    def _deserialize_operation(
        self,
        payload: dict,
        *,
        default_setup_id: str,
    ) -> OperacionPCB:
        return OperacionPCB(
            id=payload["id"],
            nombre=payload["nombre"],
            tipo=OperationType(payload["tipo"]),
            cara=BoardFace(payload["cara"]),
            orden=payload["orden"],
            setup_id=payload.get("setup_id", default_setup_id),
            archivo_gcode=payload.get("archivo_gcode"),
            nombre_archivo_original=payload.get("nombre_archivo_original"),
            tamano_archivo_bytes=payload.get("tamano_archivo_bytes"),
            sha256=payload.get("sha256"),
            tool_id=payload.get("tool_id"),
            herramienta=payload.get("herramienta"),
            analisis=self._deserialize_analysis(payload.get("analisis")),
            estado=OperationStatus(payload.get("estado", OperationStatus.ESPERANDO_ARCHIVO)),
        )

    def _deserialize_analysis(
        self,
        payload: dict | None,
    ) -> OperationAnalysis | None:
        if payload is None:
            return None
        bounds_payload = payload.get("limites")
        limits = None if bounds_payload is None else Bounds3D(**bounds_payload)
        preview_payload = payload.get("segmentos_vista_previa")
        linear_payload = payload.get("segmentos_lineales", [])
        if preview_payload is None:
            preview_payload = linear_payload
        analysis_version = payload.get("analysis_version", "legacy")
        current_analysis_version = CURRENT_ANALYSIS_VERSION
        return OperationAnalysis(
            analysis_version=analysis_version,
            current_analysis_version=current_analysis_version,
            limites=limits,
            avances_mm_min=tuple(payload.get("avances_mm_min", [])),
            profundidad_min_mm=payload.get("profundidad_min_mm"),
            profundidad_max_mm=payload.get("profundidad_max_mm"),
            cantidad_movimientos=payload.get("cantidad_movimientos", 0),
            comandos_desconocidos=tuple(payload.get("comandos_desconocidos", [])),
            comandos_no_compatibles=tuple(payload.get("comandos_no_compatibles", [])),
            acciones_husillo=tuple(payload.get("acciones_husillo", [])),
            cambios_herramienta=tuple(payload.get("cambios_herramienta", [])),
            unidades_detectadas=tuple(payload.get("unidades_detectadas", ["mm"])),
            modos_posicionamiento=tuple(payload.get("modos_posicionamiento", ["absolute"])),
            incidencias=tuple(
                AnalysisIssue(
                    severidad=IssueSeverity(item["severidad"]),
                    codigo=item["codigo"],
                    mensaje=item["mensaje"],
                    linea=item.get("linea"),
                    comando=item.get("comando"),
                )
                for item in payload.get("incidencias", [])
            ),
            analisis_incompleto=payload.get("analisis_incompleto", False),
            cabe_en_material=payload.get("cabe_en_material"),
            mensaje_material=payload.get("mensaje_material"),
            segmentos_lineales=tuple(self._deserialize_segment(segment) for segment in linear_payload),
            segmentos_vista_previa=tuple(self._deserialize_segment(segment) for segment in preview_payload),
            desbordes_material=tuple(
                MaterialOverflow(**overflow)
                for overflow in payload.get("desbordes_material", [])
            ),
            tolerancia_arco_mm=payload.get("tolerancia_arco_mm"),
        )

    def _deserialize_preparation(self, payload: dict | None) -> OperationPreparation:
        if payload is None:
            return OperationPreparation()
        return OperationPreparation(
            origen_trabajo=self._deserialize_reference(payload.get("origen_trabajo")),
            referencia_z=self._deserialize_reference(payload.get("referencia_z")),
            region_sondeable_configurada_en=self._parse_datetime(payload.get("region_sondeable_configurada_en")),
            mapa_disponible_en=self._parse_datetime(payload.get("mapa_disponible_en")),
            mapa_validado_en=self._parse_datetime(payload.get("mapa_validado_en")),
            compensacion_previsualizada_en=self._parse_datetime(payload.get("compensacion_previsualizada_en")),
            motivo_invalidacion=payload.get("motivo_invalidacion"),
        )

    def _deserialize_reference(self, payload: dict | None) -> CoordinateReference | None:
        if payload is None:
            return None
        return CoordinateReference(
            x_mm=payload["x_mm"],
            y_mm=payload["y_mm"],
            z_mm=payload.get("z_mm"),
            confirmado_en=self._parse_datetime(payload.get("confirmado_en")),
            fuente=payload.get("fuente", "SIMULATED"),
            maquina=payload.get("maquina"),
            homed_axes=payload.get("homed_axes"),
            posicion_captura=payload.get("posicion_captura"),
            sesion=payload.get("sesion"),
        )

    def _parse_datetime(self, value: str | None):
        return None if value is None else datetime.fromisoformat(value)

    def _deserialize_segment(
        self,
        payload: dict,
    ) -> PreviewSegment:
        points_payload = payload.get("puntos") or [
            {
                "x_mm": payload["inicio_x_mm"],
                "y_mm": payload["inicio_y_mm"],
            },
            {
                "x_mm": payload["fin_x_mm"],
                "y_mm": payload["fin_y_mm"],
            },
        ]
        return PreviewSegment(
            tipo=payload["tipo"],
            tipo_movimiento=payload.get("tipo_movimiento", payload["tipo"]),
            numero_linea=payload.get("numero_linea"),
            inicio_x_mm=payload["inicio_x_mm"],
            inicio_y_mm=payload["inicio_y_mm"],
            fin_x_mm=payload["fin_x_mm"],
            fin_y_mm=payload["fin_y_mm"],
            z_mm=payload.get("z_mm"),
            avance_mm_min=payload.get("avance_mm_min"),
            distancia_mm=payload.get("distancia_mm", 0.0),
            advertencias=tuple(payload.get("advertencias", [])),
            puntos=tuple(PreviewPoint(**point) for point in points_payload),
        )

    def _shared_height_map_file(self, project_id: str, operation_id: str) -> Path | None:
        try:
            project = self.load_project(project_id)
            setup_id = project.get_operation(operation_id).setup_id
        except (FileNotFoundError, ProjectValidationError):
            return None
        return self._height_map_file(project_id, setup_id)

    def _height_map_file(
        self,
        project_id: str,
        operation_id: str,
    ) -> Path:
        project_dir = self.project_dir(project_id)
        self._ensure_project_layout(project_dir)
        return project_dir / "maps" / operation_id / "height_map.json"
