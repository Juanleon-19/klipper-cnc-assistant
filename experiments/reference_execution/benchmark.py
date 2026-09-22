"""Offline profile on a disposable copy. Never starts the machine runtime.

Run from the repository with PYTHONPATH=src:. and the simulated environment.
The source directory must contain project.json, originals/ and maps/. It is
read-only to this script. Output contains counts/timings, never project data.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--right-margin', type=float, default=13)
    parser.add_argument('--top-margin', type=float, default=7)
    parser.add_argument('--cold-prepare', action='store_true',
                        help='Prepare without first reading a plan, to measure a cold cache.')
    options = parser.parse_args()
    os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false',
                      MOONRAKER_URL='http://127.0.0.1:9', MOONRAKER_WS='ws://127.0.0.1:9', SERIAL_PORT='')
    from klipper_cnc_assistant.api import create_app
    from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
    from klipper_cnc_assistant.application import compensated_gcode_service as cg, services as ps
    from tests.test_job_service import FakeRuntime, FakeAdapter
    import klipper_cnc_assistant.gcode as gcode
    results = {}
    with tempfile.TemporaryDirectory(prefix='kca-benchmark-') as temporary:
        root = Path(temporary)
        raw = json.loads((options.source / 'project.json').read_text())
        project_id = raw['id']
        target = root / 'projects' / project_id
        shutil.copytree(options.source, target)
        app = create_app(data_dir=root)
        assert app.state.machine_runtime.config.mode.value == 'simulated'
        maps = app.state.physical_map_service
        repo, job = maps.repository, app.state.job_service
        runtime = FakeRuntime()
        job.runtime = runtime
        job.adapter_factory = lambda runtime: FakeAdapter(runtime)
        setup = raw['montajes'][0]
        operation_id = sorted(raw['operaciones'], key=lambda item: item['orden'])[0]['id']
        args = dict(project_id=project_id, setup_id=setup['id'], face='superior')
        config = PhysicalMeshConfig(rows=2, columns=2, edge_margin_left_mm=0,
            edge_margin_right_mm=options.right_margin, edge_margin_bottom_mm=0,
            edge_margin_top_mm=options.top_margin)
        counters = {}
        targets = [(repo, 'load_project', 'project_load'),
                   (repo, '_deserialize_analysis', 'analysis_deserialize'),
                   (repo, '_serialize_analysis', 'analysis_serialize'),
                   (job, '_build_plan', 'plan_build'),
                   (job, '_coverage_by_operation', 'coverage'),
                   (job, '_latest_generated_by_operation', 'artifact_scan'),
                   (job, '_generated_artifact_is_current', 'artifact_validation'),
                   (maps, 'get_active', 'active_map_load'),
                   (job, '_load_plan', 'plan_load'), (job, '_save_plan', 'plan_save'),
                   (cg, 'analyze_gcode_text', 'gcode_analysis'),
                   (ps, 'analyze_gcode_text', 'gcode_analysis'),
                   (gcode, 'analyze_gcode_text', 'gcode_analysis')]

        def instrument(stack, obj, name, label):
            original = getattr(obj, name)
            def call(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    counters[label]['count'] += 1
                    counters[label]['seconds'] += time.perf_counter() - started
            stack.enter_context(patch.object(obj, name, side_effect=call))

        def measure(name, operation):
            counters.clear()
            counters.update({label: dict(count=0, seconds=0) for _, _, label in targets})
            started = time.perf_counter()
            operation()
            results[name] = dict(seconds=round(time.perf_counter() - started, 4),
                calls={label: dict(count=value['count'], seconds=round(value['seconds'], 4))
                       for label, value in counters.items()})
            print(name, json.dumps(results[name]), flush=True)

        with ExitStack() as stack:
            for obj, name, label in targets:
                instrument(stack, obj, name, label)
            measure('preview', lambda: maps.preview_mesh_from_saved_reference(
                project_id=project_id, operation_id=operation_id, config=config))
            if not options.cold_prepare:
                measure('plan_read', lambda: job.get_plan(**args))
            measure('prepare_job_cold' if options.cold_prepare else 'prepare_job',
                    lambda: job.prepare_run(**args))
            measure('prepare_job_cached', lambda: job.prepare_run(**args))
            # Start a NEW map exclusively inside the disposable working copy.
            shutil.rmtree(target / 'maps')
            setup['active_map_id'] = None
            (target / 'project.json').write_text(json.dumps(raw))
            measure('arm_probe', lambda: maps.plan_from_saved_reference(
                project_id=project_id, operation_id=operation_id, config=config, runtime=runtime))
        app.state.machine_runtime.stop()
    options.output.write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
