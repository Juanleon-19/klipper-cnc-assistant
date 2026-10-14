"""Non-motion preview/coverage/arming protocol. Motion guards remain mandatory."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from threading import RLock

from .errors import ApplicationError
from ..gcode.analysis_cache import analysis_is_current
from ..heightmap.coverage import build_coverage_report
from ..machine.physical_ownership import OwnerKind
from ..machine.physical_reference import captured_reference_map, require_current_reference
from ..storage.safe_persistence import atomic_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()).hexdigest()


class MeshPreparationService:
    def __init__(self, maps):
        self.maps, self.repository = maps, maps.repository
        self.lock = RLock()

    def _path(self, project_id, setup_id, face):
        return self.repository.project_dir(project_id) / 'reports' / 'mesh-validation' / (digest([setup_id, face]) + '.json')

    def _record(self, payload):
        path = self._path(payload['project_id'], payload['setup_id'], payload['face'])
        return json.loads(path.read_text()) if path.exists() else {}

    def operations(self, project, operation):
        # Do not silently drop operations with absent/stale analysis.
        return tuple(op for op in project.operations_for_setup(operation.setup_id) if op.cara == operation.cara)

    def dependencies(self, payload, runtime):
        from .physical_map_service import canonical_mesh_geometry
        project = self.repository.load_project(payload['project_id'])
        operation = project.get_operation(payload.get('operation_id') or payload['operation_ids'][0])
        operations = self.operations(project, operation)
        sources = []
        for op in operations:
            try:
                text, sha = self.repository.read_gcode_source(project.id, op.archivo_gcode)
            except (OSError, TypeError, UnicodeError, ApplicationError):
                text, sha = '', None
            sources.append([op.id, op.orden, op.tool_id, str(op.tool_reference_profile), sha,
                            analysis_is_current(op, text, source_sha256=sha) if sha else False])
        setup = project.get_setup(operation.setup_id)
        origin = setup.preparacion.origen_trabajo
        z = setup.preparacion.referencia_z
        return {'mesh': canonical_mesh_geometry(payload), 'sources': sources,
                'material': asdict(project.material), 'placement': setup.placement_revision,
                'origin': None if origin is None else [origin.x_mm, origin.y_mm],
                'reference_z': None if z is None else z.z_mm,
                'limits': (runtime.snapshot().get('klipper') or {}).get('limits'),
                'probe_profile': {k: v for k, v in runtime.effective_probe_profile_payload(payload.get('probe_config')).items() if k.startswith('effective_') or k == 'source'}}

    def preview(self, *, project_id, operation_id, config, runtime):
        payload = self.maps.preview_mesh_from_saved_reference(project_id=project_id, operation_id=operation_id, config=config)
        payload['mesh_fingerprint'] = digest(self.dependencies(payload, runtime))
        return payload

    def evaluate(self, payload, runtime):
        dependencies = self.dependencies(payload, runtime)
        issues, warnings = [], []
        def issue(code, reason, action, **detail):
            issues.append({'code': code, 'reason': reason, 'recommended_action': action, **detail})
        project = self.repository.load_project(payload['project_id'])
        operations = self.operations(project, project.get_operation(payload.get('operation_id') or payload['operation_ids'][0]))
        analyses = []
        for op, source in zip(operations, dependencies['sources']):
            if not source[-1]:
                issue('ANALYSIS_STALE', 'El análisis G-code falta o no corresponde al archivo vigente.',
                      'Analice de nuevo el archivo y regenere la vista previa.', operation_id=op.id, operation_name=op.nombre)
            elif (op.analisis.analisis_incompleto
                  or op.analisis.tiene_errores_criticos or not op.analisis.segmentos_vista_previa):
                issue('ANALYSIS_UNSAFE', 'El análisis no permite verificar la trayectoria completa.',
                      'Corrija el G-code y analícelo antes de validar.', operation_id=op.id, operation_name=op.nombre)
            else:
                analyses.append((op.id, op.nombre, op.analisis))
        if not operations:
            issue('NO_OPERATIONS', 'No hay operaciones para esta cara.', 'Seleccione operaciones con G-code analizado.')
        hypothetical = deepcopy(payload)
        for point in hypothetical['points']:
            if point.get('status') != 'EXCLUDED':
                point.update(z_measured=0.0, delta_z=0.0, status='MEASURED')
        height_map = self.maps.height_map_from_payload(self.maps._height_map_payload_from_points(hypothetical))
        coverage = build_coverage_report(height_map=height_map, operations=tuple(analyses), include_all_segments=True)
        for item in coverage.issues:
            if not item.numerical_only:
                issue('TOOLPATH_OUTSIDE_SUPPORT', item.reason,
                      'Ajuste región, márgenes o exclusiones para contener el segmento completo.',
                      operation_id=item.operation_id, operation_name=item.operation_name,
                      segment=item.segment_index, line=next(a for oid, _, a in analyses if oid == item.operation_id).segmentos_vista_previa[item.segment_index].numero_linea,
                      x_mm=item.x_mm, y_mm=item.y_mm,
                      distance_outside_mm=item.distance_mm)
        limits = dependencies['limits'] or {}
        origin, reference_z = dependencies['origin'], dependencies['reference_z']
        if origin is None or reference_z is None:
            issue('TRANSFORM_UNAVAILABLE', 'Faltan origen CNC o referencia Z para la transformación PCB → CNC.', 'Complete la referencia física antes de validar límites.')
        try:
            limits_valid = all(axis in limits and all(math.isfinite(float(limits[axis][k])) for k in ('min', 'max')) and limits[axis]['min'] < limits[axis]['max'] for axis in 'xyz')
        except (TypeError, KeyError, ValueError):
            limits_valid = False
        if not limits_valid:
            issue('LIMITS_UNAVAILABLE', 'No hay límites Klipper verificables.', 'Conecte y observe el runtime antes de validar.')
        else:
            for point in payload['points']:
                if point.get('status') == 'EXCLUDED':
                    continue
                for axis in 'xy':
                    value = point.get(axis + '_machine')
                    if value is None or not math.isfinite(float(value)) or not limits[axis]['min'] <= value <= limits[axis]['max']:
                        issue('MESH_POINT_OUTSIDE_LIMITS', f'Punto de malla fuera del límite {axis.upper()}.',
                              'Revise origen, montaje y región de sondeo.', point=point['index'], x_mm=point.get('x_machine'), y_mm=point.get('y_machine'))
            safe_z = (payload.get('probe_config') or {}).get('safe_z_mm')
            if reference_z is None or safe_z is None or not limits['z']['min'] <= reference_z + safe_z <= limits['z']['max']:
                issue('SAFE_Z_OUTSIDE_LIMITS', 'Z de contacto + separación segura fuera del recorrido Z.', 'Ajuste la separación segura dentro de los límites físicos.')
        return {'coverage_valid': not issues, 'validated_at': datetime.now(timezone.utc).isoformat(),
                'mesh_fingerprint': digest(dependencies), 'operations_checked': len(operations),
                'segments_checked': sum(len(a.segmentos_vista_previa) for _, _, a in analyses),
                'blocking_issues': issues[:200], 'warnings': warnings}

    def validate(self, *, project_id, operation_id, config, runtime, preview_fingerprint):
        with self.lock:
            payload = self.preview(project_id=project_id, operation_id=operation_id, config=config, runtime=runtime)
            result = self.evaluate(payload, runtime)
            if not preview_fingerprint or preview_fingerprint != result['mesh_fingerprint']:
                result['coverage_valid'] = False
                result['blocking_issues'].insert(0, {'code': 'PREVIEW_STALE', 'reason': 'Vista previa ausente u obsoleta.', 'recommended_action': 'Genere de nuevo la vista previa.'})
            atomic_json(self._path(project_id, payload['setup_id'], payload['face']), result, durable=True)
            return result

    def readiness(self, *, project_id, operation_id, config, runtime, preview_fingerprint):
        reasons = []
        def block(code, message, action):
            reasons.append({'code': code, 'message': message, 'action': action, 'severity': 'ERROR'})
        owner = runtime.physical_ownership.snapshot()
        snapshot = runtime.snapshot()
        if owner.get('recovery_pending') or snapshot.get('recovery_pending'):
            block('PHYSICAL_RECOVERY_REQUIRED', 'Recuperación física pendiente.', 'Primero verifique reposo y recupere controles; después compruebe la referencia.')
        if not (snapshot.get('moonraker') or {}).get('http_connected') or not (snapshot.get('klipper') or {}).get('ready'):
            block('RUNTIME_DISCONNECTED', 'El runtime no está conectado y listo.', 'Conecte Moonraker y compruebe Klipper.')
        if owner.get('active') or snapshot.get('active_operation'):
            block('PHYSICAL_OWNER_ACTIVE', 'Otro worker o movimiento conserva el control físico.', 'Espere a su cierre seguro antes de armar.')
        payload = self.preview(project_id=project_id, operation_id=operation_id, config=config, runtime=runtime)
        record = self._record(payload)
        if not preview_fingerprint or preview_fingerprint != payload['mesh_fingerprint']:
            block('PREVIEW_STALE', 'Vista previa ausente u obsoleta.', 'Genere de nuevo la vista previa y valide cobertura.')
        if not record.get('coverage_valid') or record.get('mesh_fingerprint') != payload['mesh_fingerprint']:
            block('COVERAGE_NOT_VALIDATED', 'La cobertura de esta vista previa no está validada.', 'Ejecute el paso 2: Validar cobertura.')
        try:
            project = self.repository.load_project(project_id)
            operation = project.get_operation(operation_id)
            evidence = project.get_setup(operation.setup_id).preparacion.physical_reference
            if not evidence:
                raise ValueError('Falta una referencia Z con evidencia de sesión física vigente.')
            capture = captured_reference_map(project, operation, evidence)
            token = require_current_reference(self.repository, runtime, project_id, operation_id, capture, project=project, capture_only=True)
            previous = self.maps._latest_surface_map(project_id, operation)
            if (previous and previous.get('status') != 'MESH_COMPLETE'
                    and any(p.get('status') == 'MEASURED' and p.get('role') != 'REFERENCE' for p in previous.get('points', []))
                    and previous.get('armed_reference_token') != token):
                block('ACQUISITION_REFERENCE_CHANGED', 'La referencia cambió desde los puntos ya medidos.',
                      'Use Modificar y rehacer malla para conservar la referencia vigente y descartar muestras incompatibles.')
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            block('REFERENCE_INVALID', str(error), 'Verifique sesión e instalación; capture una nueva Z solo si la referencia no sigue vigente.')
        return {'can_arm': not reasons, 'blocking_reasons': reasons, 'mesh_fingerprint': payload['mesh_fingerprint']}

    def arm(self, *, project_id, operation_id, config, runtime, preview_fingerprint):
        with self.lock:
            result = self.readiness(project_id=project_id, operation_id=operation_id, config=config, runtime=runtime, preview_fingerprint=preview_fingerprint)
            if not result['can_arm']:
                raise ApplicationError('; '.join(r['message'] + ' ' + r['action'] for r in result['blocking_reasons']))
            # Reserve the shared coordinator against concurrent motion/reset.
            # This preparation lease never dispatches a physical command.
            permit = runtime.physical_ownership.acquire(OwnerKind.MESH, 'mesh-preparation')
            try:
                payload = self.maps.plan_from_saved_reference(project_id=project_id, operation_id=operation_id, config=config, runtime=runtime)
                payload['operation_id'] = operation_id
                current = digest(self.dependencies(payload, runtime))
                if current != preview_fingerprint:
                    raise ApplicationError('El contexto cambió al armar. Regenere vista previa y validación; no se autoriza inicio.')
                payload['mesh_fingerprint'] = current
                payload['preflight_validation'] = self._record(payload)
                payload['armed_reference_token'] = deepcopy(require_current_reference(self.repository, runtime, project_id, operation_id, payload))
                payload['armed'] = True
                self.maps._save(project_id, payload['map_id'], payload)
                return payload
            finally:
                runtime.physical_ownership.release(permit, quiescent=True)

    def rebuild(self, *, project_id, map_id, runtime):
        with self.lock:
            permit = runtime.physical_ownership.acquire(OwnerKind.MESH, 'mesh-rebuild')
            try:
                return self.maps.repeat_measurement(project_id=project_id, map_id=map_id, runtime=runtime)
            finally:
                runtime.physical_ownership.release(permit, quiescent=True)

    def require_armed(self, payload, runtime):
        proof = payload.get('preflight_validation') or {}
        if not payload.get('armed') or not proof.get('coverage_valid'):
            raise ApplicationError('La malla no está armada. Genere vista previa, valide cobertura y arme el sondeo.')
        current = digest(self.dependencies(payload, runtime))
        if current != proof.get('mesh_fingerprint') or current != payload.get('mesh_fingerprint'):
            raise ApplicationError('La malla armada está obsoleta; regenere preview, valide y arme de nuevo.')
        require_current_reference(self.repository, runtime, payload['project_id'], payload['operation_id'], payload,
                                  expected_token=payload.get('armed_reference_token'))
