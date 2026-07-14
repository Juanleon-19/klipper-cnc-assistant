from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum

from .errors import ProjectValidationError


PROJECT_SCHEMA_VERSION = "1.4"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationType(StrEnum):
    FRESADO_SUPERIOR = "fresado_superior"
    FRESADO_INFERIOR = "fresado_inferior"
    CONTORNO = "contorno"
    PERSONALIZADO = "personalizado"
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
    ESPERANDO_ARCHIVO = "esperando archivo"
    LISTA_PARA_ANALIZAR = "lista para analizar"
    VALIDA = "valida"
    CON_ADVERTENCIAS = "con advertencias"
    BLOQUEADA_POR_ERRORES = "bloqueada por errores"


class IssueSeverity(StrEnum):
    INFORMACION = "informacion"
    ADVERTENCIA = "advertencia"
    ERROR_CRITICO = "error critico"


class PreparationState(StrEnum):
    SIN_INICIAR = "sin_iniciar"
    REFERENCIA_MAQUINA_PENDIENTE = "referencia_maquina_pendiente"
    REFERENCIA_MAQUINA_CONFIRMADA = "referencia_maquina_confirmada"
    ORIGEN_XY_PENDIENTE = "origen_xy_pendiente"
    ORIGEN_XY_CONFIRMADO = "origen_xy_confirmado"
    REFERENCIA_Z_PENDIENTE = "referencia_z_pendiente"
    REFERENCIA_Z_CONFIRMADA = "referencia_z_confirmada"
    REGION_SONDEABLE_CONFIGURADA = "region_sondeable_configurada"
    MAPA_DISPONIBLE = "mapa_disponible"
    MAPA_VALIDADO = "mapa_validado"
    COMPENSACION_PREVISUALIZADA = "compensacion_previsualizada"


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
        if self.espesor_mm is not None and self.espesor_mm <= 0:
            raise ProjectValidationError(
                "El espesor del material debe ser positivo."
            )


@dataclass(frozen=True)
class AgujeroAlineacion:
    x_mm: float
    y_mm: float
    diametro_mm: float | None = None

    def __post_init__(self) -> None:
        if self.diametro_mm is not None and self.diametro_mm <= 0:
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
class PreviewPoint:
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class MaterialOverflow:
    eje: str
    direccion: str
    limite_mm: float
    valor_mm: float
    exceso_mm: float


@dataclass(frozen=True)
class CoordinateReference:
    x_mm: float
    y_mm: float
    z_mm: float | None = None
    confirmado_en: datetime | None = None


@dataclass(frozen=True)
class OperationPreparation:
    origen_trabajo: CoordinateReference | None = None
    referencia_z: CoordinateReference | None = None
    region_sondeable_configurada_en: datetime | None = None
    mapa_disponible_en: datetime | None = None
    mapa_validado_en: datetime | None = None
    compensacion_previsualizada_en: datetime | None = None
    motivo_invalidacion: str | None = None


@dataclass(frozen=True)
class MontajePCB:
    id: str
    nombre: str
    orden: int
    preparacion: OperationPreparation = field(default_factory=OperationPreparation)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ProjectValidationError("El montaje debe tener un identificador.")
        if not self.nombre.strip():
            raise ProjectValidationError("El montaje debe tener un nombre.")
        if self.orden < 0:
            raise ProjectValidationError("El orden del montaje no puede ser negativo.")


@dataclass(frozen=True)
class PreviewSegment:
    tipo: str
    tipo_movimiento: str
    numero_linea: int | None
    inicio_x_mm: float
    inicio_y_mm: float
    fin_x_mm: float
    fin_y_mm: float
    z_mm: float | None = None
    avance_mm_min: float | None = None
    distancia_mm: float = 0.0
    advertencias: tuple[str, ...] = ()
    puntos: tuple[PreviewPoint, ...] = ()

    @property
    def desde(self) -> PreviewPoint:
        return PreviewPoint(
            x_mm=self.inicio_x_mm,
            y_mm=self.inicio_y_mm,
        )

    @property
    def hasta(self) -> PreviewPoint:
        return PreviewPoint(
            x_mm=self.fin_x_mm,
            y_mm=self.fin_y_mm,
        )


@dataclass(frozen=True)
class OperationAnalysis:
    analysis_version: str
    current_analysis_version: str
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
    modos_posicionamiento: tuple[str, ...] = ("absolute",)
    incidencias: tuple[AnalysisIssue, ...] = ()
    analisis_incompleto: bool = False
    cabe_en_material: bool | None = None
    mensaje_material: str | None = None
    segmentos_lineales: tuple[PreviewSegment, ...] = ()
    segmentos_vista_previa: tuple[PreviewSegment, ...] = ()
    desbordes_material: tuple[MaterialOverflow, ...] = ()
    tolerancia_arco_mm: float | None = None

    @property
    def analisis_desactualizado(self) -> bool:
        return self.analysis_version != self.current_analysis_version

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

    @property
    def tiene_advertencias(self) -> bool:
        return any(
            issue.severidad == IssueSeverity.ADVERTENCIA
            for issue in self.incidencias
        )

    @property
    def comandos_manuales(self) -> tuple[str, ...]:
        commands: list[str] = []
        for command in (
            *self.acciones_husillo,
            *self.cambios_herramienta,
        ):
            if command not in commands:
                commands.append(command)
        return tuple(commands)


@dataclass(frozen=True)
class OperacionPCB:
    id: str
    nombre: str
    tipo: OperationType
    cara: BoardFace
    orden: int
    setup_id: str = "setup-main"
    archivo_gcode: str | None = None
    nombre_archivo_original: str | None = None
    tamano_archivo_bytes: int | None = None
    sha256: str | None = None
    tool_id: str | None = None
    herramienta: str | None = None
    analisis: OperationAnalysis | None = None
    estado: OperationStatus = OperationStatus.ESPERANDO_ARCHIVO

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ProjectValidationError(
                "La operacion debe tener un identificador."
            )
        if not self.nombre.strip():
            raise ProjectValidationError(
                "La operacion debe tener un nombre."
            )
        if not self.setup_id.strip():
            raise ProjectValidationError(
                "La operacion debe pertenecer a un montaje."
            )
        if self.orden < 0:
            raise ProjectValidationError(
                "El orden de la operacion no puede ser negativo."
            )
        if self.tamano_archivo_bytes is not None and self.tamano_archivo_bytes < 0:
            raise ProjectValidationError(
                "El tamano del archivo no puede ser negativo."
            )

    def with_gcode(
        self,
        *,
        archivo_gcode: str,
        nombre_archivo_original: str,
        tamano_archivo_bytes: int,
        sha256: str,
    ) -> "OperacionPCB":
        return replace(
            self,
            archivo_gcode=archivo_gcode,
            nombre_archivo_original=nombre_archivo_original,
            tamano_archivo_bytes=tamano_archivo_bytes,
            sha256=sha256,
            analisis=None,
            estado=OperationStatus.LISTA_PARA_ANALIZAR,
        )

    def without_gcode(self) -> "OperacionPCB":
        return replace(
            self,
            archivo_gcode=None,
            nombre_archivo_original=None,
            tamano_archivo_bytes=None,
            sha256=None,
            analisis=None,
            estado=OperationStatus.ESPERANDO_ARCHIVO,
        )

    def with_analysis(
        self,
        analisis: OperationAnalysis,
    ) -> "OperacionPCB":
        if analisis.tiene_errores_criticos:
            estado = OperationStatus.BLOQUEADA_POR_ERRORES
        elif analisis.tiene_advertencias or analisis.analisis_incompleto:
            estado = OperationStatus.CON_ADVERTENCIAS
        else:
            estado = OperationStatus.VALIDA
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
    montajes: tuple[MontajePCB, ...] = field(
        default_factory=lambda: (
            MontajePCB(id="setup-main", nombre="Montaje principal", orden=0),
        )
    )
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
        self._validate_setups()
        self._validate_operations()

    def _validate_setups(self) -> None:
        if not self.montajes:
            raise ProjectValidationError("El proyecto debe tener al menos un montaje.")
        ids = [setup.id for setup in self.montajes]
        orders = [setup.orden for setup in self.montajes]
        if len(ids) != len(set(ids)):
            raise ProjectValidationError("No se permiten montajes con el mismo identificador.")
        if len(orders) != len(set(orders)):
            raise ProjectValidationError("No se permiten montajes con el mismo orden.")

    def _validate_operations(self) -> None:
        ids: set[str] = set()
        orders: set[tuple[str, int]] = set()
        setup_ids = {setup.id for setup in self.montajes}
        for operacion in self.operaciones:
            if operacion.id in ids:
                raise ProjectValidationError(
                    "No se permiten operaciones con el mismo identificador."
                )
            ids.add(operacion.id)
            if operacion.setup_id not in setup_ids:
                raise ProjectValidationError(
                    f"La operacion {operacion.id} pertenece a un montaje inexistente."
                )
            order_key = (operacion.setup_id, operacion.orden)
            if order_key in orders:
                raise ProjectValidationError(
                    "No se permiten operaciones con el mismo orden dentro de un montaje."
                )
            orders.add(order_key)
            if (
                not self.configuracion_alineacion.doble_cara
                and operacion.cara == BoardFace.INFERIOR
            ):
                raise ProjectValidationError(
                    "Una PCB de una cara no puede tener operaciones en la cara inferior."
                )

    @property
    def estado_general(self) -> str:
        if not self.operaciones:
            return "sin configurar"
        estados = {operacion.estado for operacion in self.operaciones}
        if OperationStatus.BLOQUEADA_POR_ERRORES in estados:
            return "bloqueado por errores"
        if OperationStatus.CON_ADVERTENCIAS in estados:
            return "con advertencias"
        if OperationStatus.ESPERANDO_ARCHIVO in estados:
            return "esperando archivo"
        if OperationStatus.LISTA_PARA_ANALIZAR in estados:
            return "pendiente de analisis"
        return "valido"

    def add_operation(
        self,
        operacion: OperacionPCB,
    ) -> "ProyectoPCB":
        if any(current.id == operacion.id for current in self.operaciones):
            raise ProjectValidationError(
                f"La operacion '{operacion.id}' ya existe."
            )
        if any(
            current.setup_id == operacion.setup_id and current.orden == operacion.orden
            for current in self.operaciones
        ):
            raise ProjectValidationError(
                f"Ya existe una operacion con orden {operacion.orden} en ese montaje."
            )
        if not any(setup.id == operacion.setup_id for setup in self.montajes):
            raise ProjectValidationError(
                f"El montaje {operacion.setup_id} no existe."
            )
        if (
            not self.configuracion_alineacion.doble_cara
            and operacion.cara == BoardFace.INFERIOR
        ):
            raise ProjectValidationError(
                "Una PCB de una cara no puede tener operaciones en la cara inferior."
            )
        operaciones = tuple(
            sorted(
                (*self.operaciones, operacion),
                key=lambda item: (self.get_setup(item.setup_id).orden, item.orden),
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
        operations.sort(key=lambda item: (self.get_setup(item.setup_id).orden, item.orden))
        updated_project = replace(
            self,
            operaciones=tuple(operations),
            actualizado_en=utc_now(),
        )
        updated_project._validate_operations()
        return updated_project

    def update_metadata(
        self,
        *,
        nombre: str,
        material: MaterialBruto,
        configuracion_alineacion: ConfiguracionAlineacion,
    ) -> "ProyectoPCB":
        updated = replace(
            self,
            nombre=nombre,
            material=material,
            configuracion_alineacion=configuracion_alineacion,
            actualizado_en=utc_now(),
        )
        updated._validate_operations()
        return updated

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


    def get_setup(self, setup_id: str) -> MontajePCB:
        for setup in self.montajes:
            if setup.id == setup_id:
                return setup
        raise ProjectValidationError(f"El montaje {setup_id} no existe.")

    def setup_for_operation(self, operation_id: str) -> MontajePCB:
        return self.get_setup(self.get_operation(operation_id).setup_id)

    def operations_for_setup(self, setup_id: str) -> tuple[OperacionPCB, ...]:
        self.get_setup(setup_id)
        return tuple(
            sorted(
                (item for item in self.operaciones if item.setup_id == setup_id),
                key=lambda item: item.orden,
            )
        )

    def add_setup(self, setup: MontajePCB) -> "ProyectoPCB":
        if any(current.id == setup.id for current in self.montajes):
            raise ProjectValidationError(f"El montaje {setup.id} ya existe.")
        if any(current.orden == setup.orden for current in self.montajes):
            raise ProjectValidationError(f"Ya existe un montaje con orden {setup.orden}.")
        return replace(
            self,
            montajes=tuple(sorted((*self.montajes, setup), key=lambda item: item.orden)),
            actualizado_en=utc_now(),
        )

    def replace_setup(self, setup: MontajePCB) -> "ProyectoPCB":
        setups = tuple(setup if current.id == setup.id else current for current in self.montajes)
        if all(current.id != setup.id for current in self.montajes):
            raise ProjectValidationError(f"El montaje {setup.id} no existe.")
        updated = replace(
            self,
            montajes=tuple(sorted(setups, key=lambda item: item.orden)),
            actualizado_en=utc_now(),
        )
        updated._validate_setups()
        return updated


@dataclass(frozen=True)
class MachineSessionStatus:
    estado: str
    home_realizado: bool
    referencia_maquina_confirmada_en: datetime | None
    z_en_altura_segura: bool
    herramienta_en_centro_cama: bool
    material_montado: bool
    origen_xy_definido: bool
    cero_z_capturado: bool
    operaciones_permitidas: tuple[str, ...]
    z_puede_bajar_durante: tuple[str, ...]
