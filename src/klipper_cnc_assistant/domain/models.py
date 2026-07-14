from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum

from .errors import ProjectValidationError


PROJECT_SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationType(StrEnum):
    AISLAMIENTO = "aislamiento"
    LIMPIEZA_COBRE = "limpieza de cobre"
    TALADRADO = "taladrado"
    AGUJEROS_ALINEACION = "agujeros de alineacion"
    CORTE_EXTERIOR = "corte exterior"
    PERSONALIZADA = "personalizada"


class BoardFace(StrEnum):
    SUPERIOR = "superior"
    INFERIOR = "inferior"


class FlipAxis(StrEnum):
    X = "x"
    Y = "y"


class OperationStatus(StrEnum):
    BORRADOR = "borrador"
    GCODE_CARGADO = "gcode cargado"
    ANALISIS_LISTO = "analisis listo"
    ERROR = "error"


class IssueSeverity(StrEnum):
    ADVERTENCIA = "advertencia"
    ERROR_CRITICO = "error critico"


@dataclass(frozen=True)
class MaterialBruto:
    ancho_mm: float
    alto_mm: float
    espesor_mm: float | None = None

    def __post_init__(self) -> None:
        if self.ancho_mm <= 0:
            raise ProjectValidationError(
                "El ancho del material debe ser positivo."
            )
        if self.alto_mm <= 0:
            raise ProjectValidationError(
                "El alto del material debe ser positivo."
            )
        if (
            self.espesor_mm is not None
            and self.espesor_mm <= 0
        ):
            raise ProjectValidationError(
                "El espesor del material debe ser positivo."
            )


@dataclass(frozen=True)
class AgujeroAlineacion:
    x_mm: float
    y_mm: float
    diametro_mm: float | None = None

    def __post_init__(self) -> None:
        if (
            self.diametro_mm is not None
            and self.diametro_mm <= 0
        ):
            raise ProjectValidationError(
                "El diametro del agujero de alineacion debe ser positivo."
            )


@dataclass(frozen=True)
class ConfiguracionAlineacion:
    doble_cara: bool = False
    eje_volteo: FlipAxis | None = None
    agujeros_alineacion: tuple[AgujeroAlineacion, ...] = ()

    def __post_init__(self) -> None:
        if self.doble_cara and self.eje_volteo is None:
            raise ProjectValidationError(
                "Una PCB de doble cara requiere un eje de volteo."
            )
        if not self.doble_cara and self.eje_volteo is not None:
            raise ProjectValidationError(
                "El eje de volteo solo aplica a proyectos de doble cara."
            )


@dataclass(frozen=True)
class AnalysisIssue:
    severidad: IssueSeverity
    codigo: str
    mensaje: str
    linea: int | None = None
    comando: str | None = None


@dataclass(frozen=True)
class Bounds3D:
    min_x_mm: float
    max_x_mm: float
    min_y_mm: float
    max_y_mm: float
    min_z_mm: float
    max_z_mm: float

    @property
    def ancho_mm(self) -> float:
        return self.max_x_mm - self.min_x_mm

    @property
    def alto_mm(self) -> float:
        return self.max_y_mm - self.min_y_mm


@dataclass(frozen=True)
class OperationAnalysis:
    limites: Bounds3D | None
    avances_mm_min: tuple[float, ...] = ()
    profundidad_min_mm: float | None = None
    profundidad_max_mm: float | None = None
    cantidad_movimientos: int = 0
    comandos_desconocidos: tuple[str, ...] = ()
    comandos_no_compatibles: tuple[str, ...] = ()
    acciones_husillo: tuple[str, ...] = ()
    cambios_herramienta: tuple[str, ...] = ()
    unidades_detectadas: tuple[str, ...] = ("mm",)
    modos_posicionamiento: tuple[str, ...] = ("absoluto",)
    incidencias: tuple[AnalysisIssue, ...] = ()
    analisis_incompleto: bool = False
    cabe_en_material: bool | None = None
    mensaje_material: str | None = None

    @property
    def ancho_mm(self) -> float | None:
        if self.limites is None:
            return None
        return self.limites.ancho_mm

    @property
    def alto_mm(self) -> float | None:
        if self.limites is None:
            return None
        return self.limites.alto_mm

    @property
    def tiene_errores_criticos(self) -> bool:
        return any(
            issue.severidad == IssueSeverity.ERROR_CRITICO
            for issue in self.incidencias
        )


@dataclass(frozen=True)
class OperacionPCB:
    id: str
    nombre: str
    tipo: OperationType
    cara: BoardFace
    orden: int
    archivo_gcode: str | None = None
    sha256: str | None = None
    herramienta: str | None = None
    analisis: OperationAnalysis | None = None
    estado: OperationStatus = OperationStatus.BORRADOR

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ProjectValidationError(
                "La operacion debe tener un identificador."
            )
        if not self.nombre.strip():
            raise ProjectValidationError(
                "La operacion debe tener un nombre."
            )
        if self.orden < 0:
            raise ProjectValidationError(
                "El orden de la operacion no puede ser negativo."
            )

    def with_gcode(
        self,
        archivo_gcode: str,
        sha256: str,
    ) -> "OperacionPCB":
        return replace(
            self,
            archivo_gcode=archivo_gcode,
            sha256=sha256,
            estado=OperationStatus.GCODE_CARGADO,
        )

    def with_analysis(
        self,
        analisis: OperationAnalysis,
    ) -> "OperacionPCB":
        estado = OperationStatus.ANALISIS_LISTO
        if analisis.tiene_errores_criticos:
            estado = OperationStatus.ERROR
        return replace(
            self,
            analisis=analisis,
            estado=estado,
        )


@dataclass(frozen=True)
class ProyectoPCB:
    id: str
    nombre: str
    material: MaterialBruto
    operaciones: tuple[OperacionPCB, ...] = ()
    creado_en: datetime = field(default_factory=utc_now)
    actualizado_en: datetime = field(default_factory=utc_now)
    version_esquema: str = PROJECT_SCHEMA_VERSION
    configuracion_alineacion: ConfiguracionAlineacion = field(
        default_factory=ConfiguracionAlineacion
    )

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ProjectValidationError(
                "El proyecto debe tener un identificador."
            )
        if not self.nombre.strip():
            raise ProjectValidationError(
                "El proyecto debe tener un nombre."
            )
        self._validate_operations()

    def _validate_operations(self) -> None:
        ids = set()
        orders = set()
        for operacion in self.operaciones:
            if operacion.id in ids:
                raise ProjectValidationError(
                    "No se permiten operaciones con el mismo identificador."
                )
            ids.add(operacion.id)
            if operacion.orden in orders:
                raise ProjectValidationError(
                    "No se permiten operaciones con el mismo orden."
                )
            orders.add(operacion.orden)

    def add_operation(
        self,
        operacion: OperacionPCB,
    ) -> "ProyectoPCB":
        if any(
            current.id == operacion.id
            for current in self.operaciones
        ):
            raise ProjectValidationError(
                f"La operacion '{operacion.id}' ya existe."
            )
        if any(
            current.orden == operacion.orden
            for current in self.operaciones
        ):
            raise ProjectValidationError(
                f"Ya existe una operacion con orden {operacion.orden}."
            )
        operaciones = tuple(
            sorted(
                (*self.operaciones, operacion),
                key=lambda item: item.orden,
            )
        )
        return replace(
            self,
            operaciones=operaciones,
            actualizado_en=utc_now(),
        )

    def remove_operation(
        self,
        operation_id: str,
    ) -> "ProyectoPCB":
        operaciones = tuple(
            item
            for item in self.operaciones
            if item.id != operation_id
        )
        if len(operaciones) == len(self.operaciones):
            raise ProjectValidationError(
                f"La operacion '{operation_id}' no existe."
            )
        return replace(
            self,
            operaciones=operaciones,
            actualizado_en=utc_now(),
        )

    def replace_operation(
        self,
        operacion: OperacionPCB,
    ) -> "ProyectoPCB":
        updated = False
        operations: list[OperacionPCB] = []
        for current in self.operaciones:
            if current.id == operacion.id:
                operations.append(operacion)
                updated = True
                continue
            operations.append(current)
        if not updated:
            raise ProjectValidationError(
                f"La operacion '{operacion.id}' no existe."
            )
        operations.sort(key=lambda item: item.orden)
        return replace(
            self,
            operaciones=tuple(operations),
            actualizado_en=utc_now(),
        )

    def get_operation(
        self,
        operation_id: str,
    ) -> OperacionPCB:
        for operacion in self.operaciones:
            if operacion.id == operation_id:
                return operacion
        raise ProjectValidationError(
            f"La operacion '{operation_id}' no existe."
        )


@dataclass(frozen=True)
class MachineSessionStatus:
    estado: str
    home_realizado: bool
    z_en_altura_segura: bool
    herramienta_en_centro_cama: bool
    material_montado: bool
    origen_xy_definido: bool
    cero_z_capturado: bool
    operaciones_permitidas: tuple[str, ...]
    z_puede_bajar_durante: tuple[str, ...]
