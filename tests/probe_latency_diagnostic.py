"""Measure synthetic startup phases; never estimates actual hardware latency."""
import json
import time
from pathlib import Path
from unittest.mock import patch
from tests.test_functional_journey import FunctionalJourneyTest
from klipper_cnc_assistant.application import reference_service

fixture = FunctionalJourneyTest()
records = {}
phase = None
started = 0.0
patches = []

def measured(obj, name, label):
    original = getattr(obj, name)
    def call(*args, **kwargs):
        current = phase
        begin = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            if current and not records[current]['emissions']:
                records[current]['checks'].append({'stage': label, 'duration_ms': round((time.perf_counter()-begin)*1000, 3)})
    p = patch.object(obj, name, call); p.start(); patches.append(p)

try:
    fixture.setUp()
    measured(fixture.runtime, '_refresh_machine', 'runtime_http_refresh')
    measured(fixture.runtime, '_require_fresh_open_probe', 'fresh_stable_open')
    measured(fixture.runtime, '_assert_serial_recent', 'serial_recent_check')
    measured(fixture.transport, 'query_objects', 'controller_http_fake')
    measured(reference_service, 'reference_context', 'reference_context_files')
    original_send = fixture.transport.send_gcode
    def send(*args, **kwargs):
        if phase:
            records[phase]['emissions'].append({'label': 'fake_transport_command', 'elapsed_ms': round((time.perf_counter()-started)*1000, 3)})
        return original_send(*args, **kwargs)
    fixture.transport.send_gcode = send
    original_post = fixture.post
    def post(path, payload=None):
        global phase, started
        if path.endswith('/probe-and-capture') or path.endswith('/execute-all'):
            phase = 'reference' if path.endswith('/probe-and-capture') else 'mesh'
            started = time.perf_counter()
            records[phase] = {'checks': [], 'emissions': []}
        result = original_post(path, payload)
        if path.endswith('/probe-and-capture'):
            records[phase]['response_ms'] = round((time.perf_counter()-started)*1000, 3)
            phase = None
        elif path.endswith('/execute-all'):
            records[phase]['response_ms'] = round((time.perf_counter()-started)*1000, 3)
        return result
    fixture.post = post
    fixture.test_reset_connect_home_prepare_reference_mesh_execution()
    for record in records.values():
        record['emissions'] = record['emissions'][:8]
        assert record['emissions'], record
    Path('/tmp/kca-probe-latency-simulated.json').write_text(json.dumps(records, indent=2))
    print(json.dumps(records, indent=2))
finally:
    for p in reversed(patches): p.stop()
    fixture.doCleanups()
