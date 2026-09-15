import os
import asyncio
import threading
from pathlib import Path

os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false',
                  MOONRAKER_URL='http://127.0.0.1:9', MOONRAKER_WS='ws://127.0.0.1:9', SERIAL_PORT='')
from klipper_cnc_assistant.api import create_app
import uvicorn
from klipper_cnc_assistant.storage.safe_persistence import snapshot_stats

app = create_app(data_dir=Path('/tmp/kca-astra-review/data'),
                 frontend_dist_dir=Path('frontend/dist').resolve())

@app.get('/api/review-metrics')
async def metrics():
    status = Path('/proc/self/status').read_text().splitlines()
    return {'rss_kib': int(next(x for x in status if x.startswith('VmRSS:')).split()[1]),
            'threads': int(next(x for x in status if x.startswith('Threads:')).split()[1]),
            'python_threads': threading.active_count(), 'async_tasks': len(asyncio.all_tasks()),
            'pid': os.getpid(), 'snapshots': snapshot_stats()}

# Put instrumentation before the SPA catch-all; never included in product.
app.router.routes.insert(0, app.router.routes.pop())
uvicorn.run(app, host='127.0.0.1', port=18765, access_log=False)
