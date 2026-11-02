#!/usr/bin/env python3
"""Operator-assisted reconciliation of ONE partial preparation reset.

No motion commands. No project writes. Requires exact journal SHA, current
project SHA observed locally, explicit interactive approval, fresh Moonraker
idle evidence, backup and server-side independent revalidation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tarfile
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get(url: str) -> dict:
    with urlopen(url, timeout=12) as response:
        return json.load(response)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError('NO-GO: ' + reason)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transaction', required=True)
    parser.add_argument('--journal-sha256', required=True)
    parser.add_argument('--apply', action='store_true',
                        help='Require typed confirmation, save backup and POST attested recovery')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    data = root / 'data'
    pending = data / 'administrative-reset.pending.json'
    require(pending.is_file(), 'El journal ya no existe; no intervenir')
    original = pending.read_bytes()
    require(sha(original) == args.journal_sha256, 'Hash del journal inesperado')
    journal = json.loads(original)
    require(journal.get('id') == args.transaction, 'Identidad cambiada')
    require(journal.get('phase') == 'HISTORY_SAVED', 'Fase diferente')
    evidence = journal.get('evidence') or {}
    project_id, setup_id = evidence.get('project_id'), evidence.get('setup_id')
    require(isinstance(project_id, str) and project_id, 'Proyecto inválido')
    require(isinstance(setup_id, str) and setup_id, 'Montaje inválido')
    require('/' not in project_id and '..' not in project_id, 'Ruta proyecto inválida')
    require('/' not in setup_id and '..' not in setup_id, 'Ruta montaje inválida')
    rel = f'projects/{project_id}/project.json'
    project_file = data / rel
    require(project_file.is_file(), 'Falta project.json')
    project_raw = project_file.read_bytes()
    project = json.loads(project_raw)
    operations = project.get('operaciones')
    require(isinstance(operations, list) and operations, 'No hay operaciones')
    operations_hash = sha(json.dumps(
        operations, sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode('utf-8'))

    # Diagnostic only. The server rechecks this under its own admin lease.
    before = journal.get('verified_after')
    require(isinstance(before, dict), 'Sin inventario verificado')
    changed, missing, added = [], [], []
    found = {}
    for p in data.rglob('*'):
        r = p.relative_to(data)
        if r.parts[0] in {'administrative-transactions', 'administrative-reset.pending.json'}:
            continue
        require(not p.is_symlink(), f'Symlink no comprobable: {r}')
        if p.is_file():
            found[str(r)] = sha(p.read_bytes())
    changed = [k for k in found.keys() & before.keys() if found[k] != before[k]]
    missing = sorted(before.keys() - found.keys())
    added = sorted(found.keys() - before.keys())
    require(changed == [rel] and not missing and not added,
            f'Archivos fuera del reset: modificados={changed}, faltantes={missing}, nuevos={added}')

    print('=== EVIDENCIA ADMINISTRATIVA ACTUAL ===')
    print('Journal:', journal['id'], sha(original))
    print('Fase:', journal['phase'])
    print('Proyecto:', project_id, 'Montaje:', setup_id)
    print('Proyecto SHA256:', sha(project_raw))
    print('Operaciones:', len(operations), 'SHA256:', operations_hash)
    for op in sorted(operations, key=lambda item: item.get('orden', 0)):
        print(' -', op.get('orden'), op.get('nombre'), op.get('tipo'),
              'perfil:', op.get('tool_reference_profile'))
    matching = [x for x in project.get('montajes', []) if x.get('id') == setup_id]
    require(len(matching) == 1, 'Montaje no único')
    setup = matching[0]
    print('Revisión:', setup.get('placement_revision'),
          'Estado:', setup.get('preparation_status'),
          'Referencia:', setup.get('active_reference_id'),
          'Mapa:', setup.get('active_map_id'))
    print('AVISO: No existe preimagen exacta. Se conservará el proyecto tal como quedó')
    print('tras el reinicio parcial, sin afirmar que se probó su equivalencia al anterior.')
    print('No se repondrán referencias ni malla. No se permite mover la CNC con esta acción.')

    base = 'http://127.0.0.1:8000'
    pending_api = get(base + '/api/machine/administrative-recovery')
    require(pending_api.get('pending') is True and pending_api.get('id') == journal['id'],
            'La API describe otra transacción')
    runtime = get(base + '/api/machine/status')
    owner = runtime.get('physical_owner') or {}
    require(runtime.get('active_operation') is None, 'Operación CNC activa')
    require(owner.get('administrative_incomplete') is True, 'Bloqueo administrativo cambió')
    require(owner.get('producer_active') is False, 'Productor físico activo')
    require(owner.get('children') == 0 and owner.get('dispatching') == 0,
            'Hay operaciones/órdenes físicas activas')
    require(not owner.get('administrative_active'), 'Administración ya activa')
    moon_url = (runtime.get('moonraker') or {}).get('url')
    require(isinstance(moon_url, str) and moon_url, 'Runtime sin Moonraker')
    status = get(moon_url.rstrip('/') +
                 '/printer/objects/query?webhooks&print_stats&virtual_sdcard&motion_report')['result']['status']
    velocity = float(status['motion_report']['live_velocity'])
    require(status['webhooks']['state'] == 'ready' and
            status['print_stats']['state'] in {'standby', 'complete', 'cancelled', 'error'} and
            status['virtual_sdcard']['is_active'] is False and
            math.isfinite(velocity) and abs(velocity) <= 0.02,
            'Klipper/Moonraker no acreditan reposo remoto')
    print('Moonraker: reposo confirmado para esta consulta; verifique también físicamente.')

    if not args.apply:
        print('PREVALIDACIÓN: SOLO LECTURA. Para conciliación explícita use --apply.')
        return
    require(sys.stdin.isatty(), 'Se requiere una terminal interactiva')
    print('\n¿Confirma que la CNC está físicamente inmóvil y acepta')
    print('conservar la preparación reiniciada sin preimagen exacta?')
    print('Introduzca la identidad completa de la transacción para confirmar:')
    typed = input('ID > ').strip()
    require(typed == journal['id'], 'Identidad no confirmada')

    # Independent durable backup before any server mutation.
    backup = Path.home() / 'kca-backups' / (
        'manual-attested-reset-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False)
    archive_path = backup / 'data.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as archive:
        archive.add(data, arcname='data', recursive=True)
    (backup / 'evidence.json').write_text(json.dumps({
        'transaction': journal['id'], 'journal_sha256': sha(original),
        'project': rel, 'project_sha256': sha(project_raw),
        'operations_sha256': operations_hash, 'operation_count': len(operations),
        'data_archive_sha256': sha(archive_path.read_bytes()),
        'status': 'BEFORE_EXPLICIT_ADMINISTRATIVE_RECOVERY',
    }, indent=2), encoding='utf-8')
    print('Respaldo:', backup)
    require(sha(pending.read_bytes()) == sha(original), 'Journal cambió durante el respaldo')
    require(sha(project_file.read_bytes()) == sha(project_raw), 'Proyecto cambió durante el respaldo')

    payload = json.dumps({
        'journal_sha256': sha(original),
        'project_sha256': sha(project_raw),
        'operations_sha256': operations_hash,
        'operation_count': len(operations),
        'acknowledgement': 'ACEPTO_CONCILIAR_RESET_PARCIAL_SIN_PREIMAGEN',
    }).encode('utf-8')
    req = Request(base + '/api/machine/administrative-recovery/' + journal['id'],
                  data=payload, method='POST',
                  headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as err:
        print('RECUPERACIÓN RECHAZADA', err.code, err.read().decode(errors='replace')[:1200])
        raise SystemExit(1) from err
    print(json.dumps(result, ensure_ascii=False, indent=2))
    require(result.get('status') == 'reconciled' and
            result.get('outcome') == 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED',
            'El servidor no confirmó el resultado de conciliación esperado')
    print('CONCILIACIÓN REGISTRADA. Revalidar reposo antes de habilitar control físico.')
    print('No se ordenó movimiento de la CNC ni se modificó el archivo del proyecto.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('ERROR:', error, file=sys.stderr)
        raise SystemExit(1) from error
