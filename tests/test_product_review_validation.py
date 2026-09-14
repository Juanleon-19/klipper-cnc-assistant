"""Product input/diagnostic regressions using temporary storage only."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from klipper_cnc_assistant.application import HeightMapService, ProjectService
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.storage import JsonProjectRepository
from klipper_cnc_assistant.execution.job_service import JobService


class ProductReviewValidationTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.repo=JsonProjectRepository(Path(temp.name));projects=ProjectService(self.repo)
        self.project=projects.create_project(nombre='Synthetic',ancho_mm=10,alto_mm=10,espesor_mm=1.6)
        self.op=projects.add_operation(project_id=self.project.id,nombre='Synthetic',tipo='aislamiento',cara='superior',orden=0)
        self.maps=HeightMapService(self.repo)
        self.samples=[{'id':f'{r}:{c}','fila':r,'columna':c,'x_mm':c*10.,'y_mm':r*10.,'z_mm':r*.1+c*.2}
                      for r in range(2) for c in range(2)]

    def import_map(self,samples):
        return self.maps.import_json_map(project_id=self.project.id,operation_id=self.op.id,content=json.dumps(samples))

    def test_duplicate_grid_slot_rejected_before_any_write(self):
        before={str(p):p.read_bytes() for p in self.repo.base_dir.rglob('*') if p.is_file()}
        self.samples.append(dict(self.samples[0],id='other',z_mm=9))
        with self.assertRaisesRegex((ApplicationError,ValueError),'repetid'):
            self.import_map(self.samples)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.repo.base_dir.rglob('*') if p.is_file()})

    def test_duplicate_sample_id_rejected(self):
        self.samples[1]['id']=self.samples[0]['id']
        with self.assertRaisesRegex((ApplicationError,ValueError),'repetid'):
            self.import_map(self.samples)

    def test_nonfinite_imports_rejected(self):
        for field in ('x_mm','y_mm','z_mm'):
            for value in (float('nan'),float('inf'),float('-inf')):
                samples=[dict(s) for s in self.samples];samples[0][field]=value
                with self.subTest(field=field,value=value),self.assertRaisesRegex((ApplicationError,ValueError),'finit'):
                    self.import_map(samples)

    def test_grid_coordinate_mismatch_rejected(self):
        self.samples[1]['x_mm']=8
        with self.assertRaisesRegex((ApplicationError,ValueError),'regular'):
            self.import_map(self.samples)

    def test_missing_value_remains_importable(self):
        self.samples[0]['z_mm']=None
        self.assertEqual(self.import_map(self.samples).estadisticas.cantidad_puntos_faltantes,1)

    def test_failed_preflight_messages_describe_missing_conditions(self):
        # Exercise the same function used by live/prepare, without starting runtime.
        runtime=SimpleNamespace(snapshot=lambda:{'mode':'SIMULATED','moonraker':{},'klipper':{}})
        service=SimpleNamespace(runtime=runtime)
        checks=JobService._build_run_checks(service,None,{'operations':[{'blocking':True}]})
        details={c['name']:c for c in checks}
        for name,expected in [('runtime_conectado','Sin conexión'),('websocket','desconectada'),
                              ('klipper_ready','no está listo'),('mapa_activo','Falta'),('operaciones_bloqueadas','bloqueadas')]:
            self.assertFalse(details[name]['ok']);self.assertIn(expected,details[name]['detail'])


if __name__=='__main__':unittest.main()
