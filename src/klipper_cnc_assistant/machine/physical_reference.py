"""Session-bound evidence of a tool measurement, never a map/session authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any
from uuid import uuid4


class PhysicalReferenceError(ValueError):
    def __init__(self, message, code="REFERENCE_CONTEXT_CHANGED"):
        super().__init__(message)
        self.code = code


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def reference_context(repository, runtime, project_id, operation_id, physical_map, installation_id=None,
                      *, project=None, token_version=1):
    if runtime is None or not callable(getattr(runtime, 'current_physical_session_id', None)):
        raise PhysicalReferenceError('No se puede verificar la sesión física de la referencia.')
    project = project if project is not None else repository.load_project(project_id)
    operation = project.get_operation(operation_id)
    setup = project.get_setup(operation.setup_id)
    if (physical_map.get('project_id') != project_id or physical_map.get('setup_id') != setup.id
            or str(physical_map.get('face')) != str(operation.cara)
            or physical_map.get('placement_revision') != setup.placement_revision
            or physical_map.get('archived_at') is not None):
        raise PhysicalReferenceError('El mapa no pertenece al contexto vigente de la referencia.')
    session = runtime.current_physical_session_id()
    if not isinstance(session, str) or not session:
        raise PhysicalReferenceError('La sesión física de la referencia no es verificable.')
    snapshot = runtime.snapshot()
    config = runtime.config
    relevant = {name: getattr(config, name) for name in dir(config)
                if name.startswith(('probe_', 'reference_', 'tool_change_', 'long_tool_', 'safe_z_', 'z_clearance_',
                                    'settle_', 'velocity_', 'serial_', 'telemetry_', 'move_'))
                and not callable(getattr(config, name))}
    # Persist only a digest of transport/configuration identity, never endpoints.
    relevant.update({name: getattr(config, name, None) for name in
                     ('physical_machine_id', 'moonraker_url', 'mode', 'spindle_control_mode')})
    klipper = snapshot.get('klipper') or {}
    relevant['machine'] = {name: klipper.get(name) for name in
                           ('limits', 'max_velocity', 'max_z_velocity', 'max_accel')}
    # V1 measures against an existing map. V2 is captured BEFORE a map exists:
    # protect the actual measurement profile, and bind acquisition settings
    # separately when arming. Never rewrite a measurement token to fit a map.
    relevant['probe_config'] = physical_map.get('reference_probe_config' if token_version == 2 else 'probe_config')
    relevant['placement_alignment'] = asdict(project.configuracion_alineacion)
    tool_id = operation.tool_id or (operation.herramienta or 'sin-herramienta').strip().lower().replace(' ', '-')
    reference = (physical_map.get('tool_references') or {}).get(tool_id) or {}
    return {
        'physical_session_id': session,
        'ownership_session_id': runtime.physical_ownership.session,
        'project_id': project_id, 'setup_id': setup.id, 'face': str(operation.cara),
        'tool_id': tool_id,
        'tool_reference_profile': str(operation.tool_reference_profile),
        'installation_id': installation_id if installation_id is not None else reference.get('installation_id'),
        'placement_revision': setup.placement_revision,
        'active_reference_id': setup.active_reference_id,
        'work_origin_fingerprint': _fingerprint(None if setup.preparacion.origen_trabajo is None else asdict(setup.preparacion.origen_trabajo)),
        'reference_point': [physical_map.get('machine_origin_x'), physical_map.get('machine_origin_y')],
        'reference_context_fingerprint': _fingerprint({
            'origin': None if setup.preparacion.origen_trabajo is None else asdict(setup.preparacion.origen_trabajo),
            'z': None if setup.preparacion.referencia_z is None else asdict(setup.preparacion.referencia_z),
            'active_reference_id': setup.active_reference_id,
        }),
        'physical_config_fingerprint': _fingerprint(relevant),
    }


@dataclass(frozen=True)
class PhysicalReferenceToken:
    context: dict[str, Any]
    reference_revision: str
    measured_at: str
    measured_position: dict[str, float]
    version: int = 1

    @classmethod
    def measured(cls, context, position, *, measured_at=None, version=1):
        coordinates = {axis: float(position[axis]) for axis in ('x_mm', 'y_mm', 'z_mm')}
        if not all(math.isfinite(value) for value in coordinates.values()):
            raise PhysicalReferenceError('La medición de referencia no es finita.')
        return cls(context, uuid4().hex, measured_at or datetime.now(timezone.utc).isoformat(), coordinates, version)

    def payload(self):
        # Context fields are explicit in persisted evidence for audit/readability.
        return {**self.context, 'reference_revision': self.reference_revision,
                'measured_at': self.measured_at, 'measured_position': self.measured_position,
                'version': self.version}


def require_current_reference(repository, runtime, project_id, operation_id, physical_map,
                              *, installation_id=None, expected_token=None, project=None,
                              capture_only=False):
    project = project if project is not None else repository.load_project(project_id)
    operation = project.get_operation(operation_id)
    tool_id = operation.tool_id or (operation.herramienta or 'sin-herramienta').strip().lower().replace(' ', '-')
    reference = (physical_map.get('tool_references') or {}).get(tool_id)
    token = reference.get('physical_reference_token') if isinstance(reference, dict) else None
    if not isinstance(token, dict) or not reference.get('valid') or type(token.get('version')) is not int or token.get('version') not in (1, 2):
        raise PhysicalReferenceError('Referencia Z sin evidencia de sesión física vigente; mida de nuevo la herramienta seleccionada.', 'REFERENCE_MEASUREMENT_REQUIRED')
    if token['version'] == 2:
        canonical = project.get_setup(operation.setup_id).preparacion.physical_reference
        if not canonical or canonical.get('reference') != reference:
            raise PhysicalReferenceError('La evidencia de referencia no coincide con la medición canónica de la herramienta; mida de nuevo.')
        if physical_map.get('reference_probe_config') != canonical.get('probe_config'):
            raise PhysicalReferenceError('El perfil de la medición de referencia cambió; mida de nuevo.')
        if not capture_only and physical_map.get('initial_reference_binding') != initial_map_binding(physical_map, token):
            raise PhysicalReferenceError('La referencia no está vinculada al mapa y perfil de sondeo vigentes; vuelva a armar el mapa.')
    elif capture_only:
        raise PhysicalReferenceError('La captura inicial no contiene evidencia canónica de sesión.')
    context = reference_context(repository, runtime, project_id, operation_id, physical_map, installation_id,
                                project=project, token_version=token['version'])
    if token.get('physical_session_id') != context['physical_session_id']:
        raise PhysicalReferenceError('La referencia Z pertenece a otra sesión física; mida de nuevo.', 'REFERENCE_SESSION_CHANGED')
    if runtime.physical_ownership.snapshot()['recovery_pending']:
        raise PhysicalReferenceError('La referencia no puede autorizar ejecución durante recuperación física.', 'REFERENCE_RECOVERY_REQUIRED')
    if any(token.get(key) != value for key, value in context.items()):
        raise PhysicalReferenceError('La sesión o el contexto físico de la referencia de herramienta cambió; mida de nuevo.')
    if not token.get('reference_revision') or not token.get('measured_at'):
        raise PhysicalReferenceError('El token de referencia está incompleto.')
    try:
        timestamp = datetime.fromisoformat(token['measured_at'])
        if timestamp.tzinfo is None:
            raise ValueError('missing timezone')
        position = token['measured_position']
        if not all(math.isfinite(float(position[axis])) for axis in ('x_mm', 'y_mm', 'z_mm')):
            raise ValueError('nonfinite')
        if (float(position['z_mm']) != float(reference['reference_z'])
                or float(position['z_mm']) != float(reference['tool_reference_z'])
                or [float(position['x_mm']), float(position['y_mm'])] != [float(reference['reference_x']), float(reference['reference_y'])]
                or token['measured_at'] != reference['measured_at']
                or [float(position['x_mm']), float(position['y_mm'])] != context['reference_point']):
            raise ValueError('measurement mismatch')
    except (KeyError, TypeError, ValueError) as error:
        raise PhysicalReferenceError('El token no corresponde a la medición canónica.') from error
    if expected_token is not None and token != expected_token:
        raise PhysicalReferenceError('La medición de herramienta cambió durante la preparación de ejecución.')
    return token


def initial_map_binding(physical_map, token):
    """Auditable map attachment; no change of tool, measurement or token fields."""
    return {'reference_revision': token['reference_revision'], 'token_sha256': _fingerprint(token),
            'tool_id': token['tool_id'], 'map_id': physical_map.get('map_id'),
            'probe_config_sha256': _fingerprint(physical_map.get('probe_config'))}


def captured_reference_map(project, operation, evidence):
    setup = project.get_setup(operation.setup_id)
    reference = evidence['reference']
    return {'project_id': project.id, 'setup_id': setup.id, 'face': str(operation.cara),
            'placement_revision': setup.placement_revision,
            'machine_origin_x': reference['reference_x'], 'machine_origin_y': reference['reference_y'],
            'reference_probe_config': evidence['probe_config'],
            'tool_references': {reference['tool_id']: reference}}
