from __future__ import annotations

from pydantic import BaseModel, Field

from klipper_cnc_assistant.domain import (
    AgujeroAlineacion,
    AnalysisIssue,
    MachineSessionStatus,
    OperacionPCB,
    OperationAnalysis,
    ProyectoPCB,
)


class HealthResponse(BaseModel):
    estado: str


class ErrorResponse(BaseModel):
    detalle: str


class MaterialRequest(BaseModel):
    ancho_mm: float = Field(gt=0)
    alto_mm: float = Field(gt=0)
    espesor_mm: float | None = Field(
        default=None,
        gt=0,
    )


class AgujeroAlineacionRequest(BaseModel):
    x_mm: float
    y_mm: float
    diametro_mm: float | None = Field(
        default=None,
        gt=0,
    )


class ProjectCreateRequest(BaseModel):
    nombre: str = Field(min_length=1)
    material: MaterialRequest
    doble_cara: bool = False
    eje_volteo: str | None = None
    agujeros_alineacion: list[
        AgujeroAlineacionRequest
    ] = Field(default_factory=list)


class OperationCreateRequest(BaseModel):
    nombre: str = Field(min_length=1)
    tipo: str
    cara: str
    orden: int = Field(ge=0)
    herramienta: str | None = None


class GCodeUploadRequest(BaseModel):
    nombre_archivo: str = Field(min_length=1)
    contenido: str


class AgujeroAlineacionResponse(BaseModel):
    x_mm: float
    y_mm: float
    diametro_mm: float | None


class MaterialResponse(BaseModel):
    ancho_mm: float
    alto_mm: float
    espesor_mm: float | None


class AnalysisIssueResponse(BaseModel):
    severidad: str
    codigo: str
    mensaje: str
    linea: int | None
    comando: str | None


class BoundsResponse(BaseModel):
    min_x_mm: float
    max_x_mm: float
    min_y_mm: float
    max_y_mm: float
    min_z_mm: float
    max_z_mm: float
    ancho_mm: float
    alto_mm: float


class OperationAnalysisResponse(BaseModel):
    limites: BoundsResponse | None
    avances_mm_min: list[float]
    profundidad_min_mm: float | None
    profundidad_max_mm: float | None
    cantidad_movimientos: int
    comandos_desconocidos: list[str]
    comandos_no_compatibles: list[str]
    acciones_husillo: list[str]
    cambios_herramienta: list[str]
    unidades_detectadas: list[str]
    modos_posicionamiento: list[str]
    incidencias: list[AnalysisIssueResponse]
    analisis_incompleto: bool
    cabe_en_material: bool | None
    mensaje_material: str | None
    tiene_errores_criticos: bool


class OperationResponse(BaseModel):
    id: str
    nombre: str
    tipo: str
    cara: str
    orden: int
    archivo_gcode: str | None
    sha256: str | None
    herramienta: str | None
    estado: str
    analisis: OperationAnalysisResponse | None


class ProjectResponse(BaseModel):
    id: str
    nombre: str
    material: MaterialResponse
    doble_cara: bool
    eje_volteo: str | None
    agujeros_alineacion: list[
        AgujeroAlineacionResponse
    ]
    operaciones: list[OperationResponse]
    creado_en: str
    actualizado_en: str
    version_esquema: str


class MachineSessionResponse(BaseModel):
    estado: str
    home_realizado: bool
    z_en_altura_segura: bool
    herramienta_en_centro_cama: bool
    material_montado: bool
    origen_xy_definido: bool
    cero_z_capturado: bool
    operaciones_permitidas: list[str]
    z_puede_bajar_durante: list[str]


def project_to_response(
    project: ProyectoPCB,
) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        nombre=project.nombre,
        material=MaterialResponse(
            ancho_mm=project.material.ancho_mm,
            alto_mm=project.material.alto_mm,
            espesor_mm=project.material.espesor_mm,
        ),
        doble_cara=project.configuracion_alineacion.doble_cara,
        eje_volteo=project.configuracion_alineacion.eje_volteo,
        agujeros_alineacion=[
            AgujeroAlineacionResponse(
                x_mm=hole.x_mm,
                y_mm=hole.y_mm,
                diametro_mm=hole.diametro_mm,
            )
            for hole in project.configuracion_alineacion.agujeros_alineacion
        ],
        operaciones=[
            operation_to_response(operation)
            for operation in project.operaciones
        ],
        creado_en=project.creado_en.isoformat(),
        actualizado_en=project.actualizado_en.isoformat(),
        version_esquema=project.version_esquema,
    )


def operation_to_response(
    operation: OperacionPCB,
) -> OperationResponse:
    return OperationResponse(
        id=operation.id,
        nombre=operation.nombre,
        tipo=operation.tipo,
        cara=operation.cara,
        orden=operation.orden,
        archivo_gcode=operation.archivo_gcode,
        sha256=operation.sha256,
        herramienta=operation.herramienta,
        estado=operation.estado,
        analisis=(
            None
            if operation.analisis is None
            else analysis_to_response(
                operation.analisis
            )
        ),
    )


def analysis_to_response(
    analysis: OperationAnalysis,
) -> OperationAnalysisResponse:
    return OperationAnalysisResponse(
        limites=(
            None
            if analysis.limites is None
            else BoundsResponse(
                min_x_mm=analysis.limites.min_x_mm,
                max_x_mm=analysis.limites.max_x_mm,
                min_y_mm=analysis.limites.min_y_mm,
                max_y_mm=analysis.limites.max_y_mm,
                min_z_mm=analysis.limites.min_z_mm,
                max_z_mm=analysis.limites.max_z_mm,
                ancho_mm=analysis.limites.ancho_mm,
                alto_mm=analysis.limites.alto_mm,
            )
        ),
        avances_mm_min=list(
            analysis.avances_mm_min
        ),
        profundidad_min_mm=analysis.profundidad_min_mm,
        profundidad_max_mm=analysis.profundidad_max_mm,
        cantidad_movimientos=analysis.cantidad_movimientos,
        comandos_desconocidos=list(
            analysis.comandos_desconocidos
        ),
        comandos_no_compatibles=list(
            analysis.comandos_no_compatibles
        ),
        acciones_husillo=list(
            analysis.acciones_husillo
        ),
        cambios_herramienta=list(
            analysis.cambios_herramienta
        ),
        unidades_detectadas=list(
            analysis.unidades_detectadas
        ),
        modos_posicionamiento=list(
            analysis.modos_posicionamiento
        ),
        incidencias=[
            issue_to_response(issue)
            for issue in analysis.incidencias
        ],
        analisis_incompleto=analysis.analisis_incompleto,
        cabe_en_material=analysis.cabe_en_material,
        mensaje_material=analysis.mensaje_material,
        tiene_errores_criticos=analysis.tiene_errores_criticos,
    )


def issue_to_response(
    issue: AnalysisIssue,
) -> AnalysisIssueResponse:
    return AnalysisIssueResponse(
        severidad=issue.severidad,
        codigo=issue.codigo,
        mensaje=issue.mensaje,
        linea=issue.linea,
        comando=issue.comando,
    )


def machine_session_to_response(
    session: MachineSessionStatus,
) -> MachineSessionResponse:
    return MachineSessionResponse(
        estado=session.estado,
        home_realizado=session.home_realizado,
        z_en_altura_segura=session.z_en_altura_segura,
        herramienta_en_centro_cama=session.herramienta_en_centro_cama,
        material_montado=session.material_montado,
        origen_xy_definido=session.origen_xy_definido,
        cero_z_capturado=session.cero_z_capturado,
        operaciones_permitidas=list(
            session.operaciones_permitidas
        ),
        z_puede_bajar_durante=list(
            session.z_puede_bajar_durante
        ),
    )
