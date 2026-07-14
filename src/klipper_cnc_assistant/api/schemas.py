from __future__ import annotations

from pydantic import BaseModel, Field

from klipper_cnc_assistant.domain import (
    AnalysisIssue,
    MachineSessionStatus,
    MontajePCB,
    MaterialOverflow,
    OperacionPCB,
    OperationAnalysis,
    PreviewPoint,
    PreviewSegment,
    ProyectoPCB,
)


class HealthResponse(BaseModel):
    estado: str
    version: str
    modo_maquina: str
    almacenamiento: str


class SystemInfoResponse(BaseModel):
    estado: str
    version_aplicacion: str
    version_python: str
    almacenamiento_disponible: bool
    estado_api: str
    modo_maquina: str
    hora_servidor: str
    backend_version: str
    frontend_build: str
    git_commit: str | None
    schema_version: str


class ErrorResponse(BaseModel):
    detalle: str


class MaterialRequest(BaseModel):
    ancho_mm: float = Field(gt=0)
    alto_mm: float = Field(gt=0)
    espesor_mm: float | None = Field(default=None, gt=0)


class AgujeroAlineacionRequest(BaseModel):
    x_mm: float
    y_mm: float
    diametro_mm: float | None = Field(default=None, gt=0)


class ProjectCreateRequest(BaseModel):
    nombre: str = Field(min_length=1)
    material: MaterialRequest
    doble_cara: bool = False
    eje_volteo: str | None = None
    agujeros_alineacion: list[AgujeroAlineacionRequest] = Field(default_factory=list)


class ProjectUpdateRequest(ProjectCreateRequest):
    pass


class SetupCreateRequest(BaseModel):
    nombre: str = Field(min_length=1)


class SetupUpdateRequest(SetupCreateRequest):
    pass


class OperationCreateRequest(BaseModel):
    nombre: str = Field(min_length=1)
    tipo: str
    cara: str | None = None
    orden: int | None = Field(default=None, ge=0)
    setup_id: str | None = None
    tool_id: str | None = None
    herramienta: str | None = None


class OperationUpdateRequest(BaseModel):
    nombre: str = Field(min_length=1)
    tool_id: str | None = None
    herramienta: str | None = None


class OperationMoveRequest(BaseModel):
    direccion: str


class GCodeUploadRequest(BaseModel):
    nombre_archivo: str = Field(min_length=1)
    contenido: str = Field(min_length=1)


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


class PointResponse(BaseModel):
    x_mm: float
    y_mm: float


class MaterialOverflowResponse(BaseModel):
    eje: str
    direccion: str
    limite_mm: float
    valor_mm: float
    exceso_mm: float


class PreviewSegmentResponse(BaseModel):
    tipo: str
    tipo_movimiento: str
    numero_linea: int | None
    inicio_x_mm: float
    inicio_y_mm: float
    fin_x_mm: float
    fin_y_mm: float
    z_mm: float | None
    avance_mm_min: float | None
    distancia_mm: float
    advertencias: list[str]
    puntos: list[PointResponse]
    desde: PointResponse
    hasta: PointResponse


class OperationAnalysisResponse(BaseModel):
    analysis_version: str
    current_analysis_version: str
    analisis_desactualizado: bool
    limites: BoundsResponse | None
    avances_mm_min: list[float]
    profundidad_min_mm: float | None
    profundidad_max_mm: float | None
    cantidad_movimientos: int
    comandos_desconocidos: list[str]
    comandos_no_compatibles: list[str]
    acciones_husillo: list[str]
    cambios_herramienta: list[str]
    comandos_manuales: list[str]
    unidades_detectadas: list[str]
    modos_posicionamiento: list[str]
    incidencias: list[AnalysisIssueResponse]
    analisis_incompleto: bool
    soporte_geometrico_incompleto: bool
    cabe_en_material: bool | None
    mensaje_material: str | None
    tiene_errores_criticos: bool
    segmentos_lineales: list[PreviewSegmentResponse]
    segmentos_vista_previa: list[PreviewSegmentResponse]
    desbordes_material: list[MaterialOverflowResponse]
    tolerancia_arco_mm: float | None


class SetupResponse(BaseModel):
    id: str
    nombre: str
    orden: int


class OperationResponse(BaseModel):
    id: str
    nombre: str
    tipo: str
    cara: str
    orden: int
    setup_id: str
    archivo_gcode: str | None
    nombre_archivo_original: str | None
    tamano_archivo_bytes: int | None
    sha256: str | None
    tool_id: str | None
    herramienta: str | None
    estado: str
    analisis: OperationAnalysisResponse | None


class ProjectResponse(BaseModel):
    id: str
    nombre: str
    material: MaterialResponse
    doble_cara: bool
    eje_volteo: str | None
    agujeros_alineacion: list[AgujeroAlineacionResponse]
    montajes: list[SetupResponse]
    operaciones: list[OperationResponse]
    creado_en: str
    actualizado_en: str
    version_esquema: str
    estado_general: str


class MachineSessionResponse(BaseModel):
    estado: str
    home_realizado: bool
    referencia_maquina_confirmada_en: str | None
    z_en_altura_segura: bool
    herramienta_en_centro_cama: bool
    material_montado: bool
    origen_xy_definido: bool
    cero_z_capturado: bool
    operaciones_permitidas: list[str]
    z_puede_bajar_durante: list[str]


class ReferenceStepResponse(BaseModel):
    id: str
    titulo: str
    estado: str
    confirmado: bool
    fecha: str | None
    detalle: str | None = None


class ReferencePointResponse(BaseModel):
    x_mm: float | None
    y_mm: float | None
    z_mm: float | None


class ReferenceWorkOriginRequest(BaseModel):
    x_mm: float | None = None
    y_mm: float | None = None


class ReferenceZRequest(BaseModel):
    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None


class ReferenceSessionResponse(BaseModel):
    estado: str
    machine_reference: dict[str, bool | str | None]
    origen_maquina: ReferencePointResponse
    origen_material: ReferencePointResponse
    origen_gcode: ReferencePointResponse
    origen_trabajo: dict[str, float | str | None] | None
    referencia_z: dict[str, float | str | None] | None
    pasos: list[ReferenceStepResponse]
    compensacion_previsualizada_en: str | None
    analysis_stale: bool
    lista_para_compensacion: bool
    bloqueos_compensacion: list[str]
    motivo_invalidacion: str | None


def project_to_response(project: ProyectoPCB) -> ProjectResponse:
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
        montajes=[setup_to_response(setup) for setup in project.montajes],
        operaciones=[operation_to_response(operation) for operation in project.operaciones],
        creado_en=project.creado_en.isoformat(),
        actualizado_en=project.actualizado_en.isoformat(),
        version_esquema=project.version_esquema,
        estado_general=project.estado_general,
    )


def setup_to_response(setup: MontajePCB) -> SetupResponse:
    return SetupResponse(id=setup.id, nombre=setup.nombre, orden=setup.orden)


def operation_to_response(operation: OperacionPCB) -> OperationResponse:
    return OperationResponse(
        id=operation.id,
        nombre=operation.nombre,
        tipo=operation.tipo,
        cara=operation.cara,
        orden=operation.orden,
        setup_id=operation.setup_id,
        archivo_gcode=operation.archivo_gcode,
        nombre_archivo_original=operation.nombre_archivo_original,
        tamano_archivo_bytes=operation.tamano_archivo_bytes,
        sha256=operation.sha256,
        tool_id=operation.tool_id,
        herramienta=operation.herramienta,
        estado=operation.estado,
        analisis=None if operation.analisis is None else analysis_to_response(operation.analisis),
    )


def analysis_to_response(analysis: OperationAnalysis) -> OperationAnalysisResponse:
    return OperationAnalysisResponse(
        analysis_version=analysis.analysis_version,
        current_analysis_version=analysis.current_analysis_version,
        analisis_desactualizado=analysis.analisis_desactualizado,
        limites=None
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
        ),
        avances_mm_min=list(analysis.avances_mm_min),
        profundidad_min_mm=analysis.profundidad_min_mm,
        profundidad_max_mm=analysis.profundidad_max_mm,
        cantidad_movimientos=analysis.cantidad_movimientos,
        comandos_desconocidos=list(analysis.comandos_desconocidos),
        comandos_no_compatibles=list(analysis.comandos_no_compatibles),
        acciones_husillo=list(analysis.acciones_husillo),
        cambios_herramienta=list(analysis.cambios_herramienta),
        comandos_manuales=list(analysis.comandos_manuales),
        unidades_detectadas=list(analysis.unidades_detectadas),
        modos_posicionamiento=list(analysis.modos_posicionamiento),
        incidencias=[issue_to_response(issue) for issue in analysis.incidencias],
        analisis_incompleto=analysis.analisis_incompleto,
        soporte_geometrico_incompleto=analysis.analisis_incompleto,
        cabe_en_material=analysis.cabe_en_material,
        mensaje_material=analysis.mensaje_material,
        tiene_errores_criticos=analysis.tiene_errores_criticos,
        segmentos_lineales=[segment_to_response(segment) for segment in analysis.segmentos_lineales],
        segmentos_vista_previa=[segment_to_response(segment) for segment in analysis.segmentos_vista_previa],
        desbordes_material=[overflow_to_response(item) for item in analysis.desbordes_material],
        tolerancia_arco_mm=analysis.tolerancia_arco_mm,
    )


def issue_to_response(issue: AnalysisIssue) -> AnalysisIssueResponse:
    return AnalysisIssueResponse(
        severidad=issue.severidad,
        codigo=issue.codigo,
        mensaje=issue.mensaje,
        linea=issue.linea,
        comando=issue.comando,
    )


def point_to_response(point: PreviewPoint) -> PointResponse:
    return PointResponse(
        x_mm=point.x_mm,
        y_mm=point.y_mm,
    )


def overflow_to_response(overflow: MaterialOverflow) -> MaterialOverflowResponse:
    return MaterialOverflowResponse(
        eje=overflow.eje,
        direccion=overflow.direccion,
        limite_mm=overflow.limite_mm,
        valor_mm=overflow.valor_mm,
        exceso_mm=overflow.exceso_mm,
    )


def segment_to_response(segment: PreviewSegment) -> PreviewSegmentResponse:
    return PreviewSegmentResponse(
        tipo=segment.tipo,
        tipo_movimiento=segment.tipo_movimiento,
        numero_linea=segment.numero_linea,
        inicio_x_mm=segment.inicio_x_mm,
        inicio_y_mm=segment.inicio_y_mm,
        fin_x_mm=segment.fin_x_mm,
        fin_y_mm=segment.fin_y_mm,
        z_mm=segment.z_mm,
        avance_mm_min=segment.avance_mm_min,
        distancia_mm=segment.distancia_mm,
        advertencias=list(segment.advertencias),
        puntos=[point_to_response(point) for point in segment.puntos],
        desde=point_to_response(segment.desde),
        hasta=point_to_response(segment.hasta),
    )


def machine_session_to_response(session: MachineSessionStatus) -> MachineSessionResponse:
    return MachineSessionResponse(
        estado=session.estado,
        home_realizado=session.home_realizado,
        referencia_maquina_confirmada_en=None if session.referencia_maquina_confirmada_en is None else session.referencia_maquina_confirmada_en.isoformat(),
        z_en_altura_segura=session.z_en_altura_segura,
        herramienta_en_centro_cama=session.herramienta_en_centro_cama,
        material_montado=session.material_montado,
        origen_xy_definido=session.origen_xy_definido,
        cero_z_capturado=session.cero_z_capturado,
        operaciones_permitidas=list(session.operaciones_permitidas),
        z_puede_bajar_durante=list(session.z_puede_bajar_durante),
    )
