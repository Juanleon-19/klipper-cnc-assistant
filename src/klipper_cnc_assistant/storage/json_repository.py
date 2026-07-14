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
    FlipAxis,
    IssueSeverity,
    MaterialBruto,
    OperationAnalysis,
    OperationStatus,
    OperationType,
    OperacionPCB,
    PreviewSegment,
    ProyectoPCB,
)


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
        for project_file in sorted(
            self.projects_dir.glob("*/project.json")
        ):
            projects.append(
                self._deserialize_project(
                    json.loads(
                        project_file.read_text(
                            encoding="utf-8"
                        )
                    )
                )
            )
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
        return self._deserialize_project(
            json.loads(
                project_file.read_text(
                    encoding="utf-8"
                )
            )
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
            existing_sha = hashlib.sha256(
                absolute_path.read_bytes()
            ).hexdigest()
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
        for relative in (
            "",
            "originals",
            "maps",
            "generated",
            "reports",
        ):
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
            "operaciones": [
                self._serialize_operation(operation)
                for operation in project.operaciones
            ],
            "creado_en": project.creado_en.isoformat(),
            "actualizado_en": project.actualizado_en.isoformat(),
            "version_esquema": project.version_esquema,
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
            "archivo_gcode": operation.archivo_gcode,
            "nombre_archivo_original": operation.nombre_archivo_original,
            "tamano_archivo_bytes": operation.tamano_archivo_bytes,
            "sha256": operation.sha256,
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
            "limites": None
            if analysis.limites is None
            else {
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
            "segmentos_lineales": [
                asdict(segment)
                for segment in analysis.segmentos_lineales
            ],
        }

    def _deserialize_project(
        self,
        payload: dict,
    ) -> ProyectoPCB:
        alignment_data = payload.get(
            "configuracion_alineacion",
            {},
        )
        return ProyectoPCB(
            id=payload["id"],
            nombre=payload["nombre"],
            material=MaterialBruto(**payload["material"]),
            operaciones=tuple(
                self._deserialize_operation(item)
                for item in payload.get("operaciones", [])
            ),
            creado_en=datetime.fromisoformat(payload["creado_en"]),
            actualizado_en=datetime.fromisoformat(payload["actualizado_en"]),
            version_esquema=payload.get("version_esquema", "1.0"),
            configuracion_alineacion=ConfiguracionAlineacion(
                doble_cara=alignment_data.get("doble_cara", False),
                eje_volteo=(
                    FlipAxis(alignment_data["eje_volteo"])
                    if alignment_data.get("eje_volteo")
                    else None
                ),
                agujeros_alineacion=tuple(
                    AgujeroAlineacion(**hole)
                    for hole in alignment_data.get(
                        "agujeros_alineacion",
                        [],
                    )
                ),
            ),
        )

    def _deserialize_operation(
        self,
        payload: dict,
    ) -> OperacionPCB:
        return OperacionPCB(
            id=payload["id"],
            nombre=payload["nombre"],
            tipo=OperationType(payload["tipo"]),
            cara=BoardFace(payload["cara"]),
            orden=payload["orden"],
            archivo_gcode=payload.get("archivo_gcode"),
            nombre_archivo_original=payload.get("nombre_archivo_original"),
            tamano_archivo_bytes=payload.get("tamano_archivo_bytes"),
            sha256=payload.get("sha256"),
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
        return OperationAnalysis(
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
            segmentos_lineales=tuple(
                PreviewSegment(**segment)
                for segment in payload.get("segmentos_lineales", [])
            ),
        )
