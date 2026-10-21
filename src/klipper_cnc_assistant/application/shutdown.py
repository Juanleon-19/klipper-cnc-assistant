"""Terminal application shutdown. Does not cancel a remote print or release a lease.

The coordinator fence is irreversible for this process. Bookkeeping locks never
cover joins or transport IO. A failed/timed-out cleanup is retained, not retried
concurrently or reported as success.
"""
import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)


class ApplicationShutdownError(RuntimeError):
    pass


class ApplicationShutdown:
    def __init__(self, runtime, producers, *, producer_timeout=5.0, cleanup_timeout=2.0):
        self.runtime = runtime
        self.producers = tuple(producers)
        self.producer_timeout = producer_timeout
        self.cleanup_timeout = cleanup_timeout
        self.started = False
        self.active_requests = 0
        self._task = None
        self._steps = {}

    async def _step(self, name, callback, timeout):
        if name not in self._steps:
            done = threading.Event()
            result = []

            def run():
                try:
                    callback()
                except BaseException as error:
                    logger.exception('Application shutdown stage failed: %s', name)
                    result.append(error)
                finally:
                    done.set()

            thread = threading.Thread(target=run, name=f'shutdown-{name}', daemon=True)
            self._steps[name] = done, result, thread
            thread.start()
        done, result, thread = self._steps[name]
        deadline = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
        if not done.is_set():
            raise ApplicationShutdownError(f'{name}: timeout; task retained, resources not released')
        if result:
            raise ApplicationShutdownError(f'{name}: {result[0]}') from result[0]

    async def close(self):
        if self._task is None:
            self.started = True
            # Fence synchronously before scheduling any asynchronous cleanup.
            self.runtime.physical_ownership.begin_shutdown()
            self._task = asyncio.create_task(self._close())
        # Cancellation of a caller cannot cancel the ordered cleanup.
        return await asyncio.shield(self._task)

    async def _close(self):
        errors = []
        for name, producer in [('runtime', self.runtime)] + [
                (f'producer-{i}', p) for i, p in enumerate(self.producers)]:
            try:
                await self._step(f'signal-{name}', producer.request_application_shutdown,
                                 self.cleanup_timeout)
            except ApplicationShutdownError as error:
                errors.append(str(error))
        if errors:
            self._fail(errors)  # Unknown producer state: no transport may close.

        def drain():
            deadline = time.monotonic() + self.producer_timeout
            while True:
                workers = [t for p in self.producers for t in p.application_workers()]
                live = [t for t in workers if t.is_alive()]
                if not live and not self.active_requests and self.runtime.application_producers_idle():
                    return
                if time.monotonic() >= deadline:
                    raise ApplicationShutdownError('Local producers/dispatch/operation remain active')
                for thread in live:
                    thread.join(min(0.01, max(0, deadline - time.monotonic())))
                if not live:
                    time.sleep(0.01)

        await self._step('drain', drain, self.producer_timeout + 0.1)
        # A retained root records physical uncertainty; it is NOT a live thread.
        # No retire/release/reconcile here: the terminal fence blocks future sends.
        runtime = self.runtime
        runtime._serial_stop.set()

        def stop_telemetry():
            if runtime._telemetry is not None:
                runtime._telemetry.stop()
            thread = runtime._telemetry_thread
            if thread is not None:
                thread.join(self.cleanup_timeout)
                if thread.is_alive():
                    raise ApplicationShutdownError('Telemetry thread still alive')

        stages = [('arduino', lambda: runtime._connection_manager.stop()
                   if runtime._connection_manager is not None else None),
                  ('telemetry', stop_telemetry),
                  ('http', lambda: runtime._client.session.close()
                   if runtime._client is not None else None)]
        for name, callback in stages:
            try:
                await self._step(name, callback, self.cleanup_timeout)
            except ApplicationShutdownError as error:
                errors.append(str(error))
        if errors:
            self._fail(errors)  # Retain process lock while any cleanup is uncertain.
        await self._step('process-lock', runtime._process_lock.close, self.cleanup_timeout)
        logger.info('Application local shutdown complete; physical recovery and persisted JobRun preserved')

    @staticmethod
    def _fail(errors):
        message = '; '.join(errors)
        logger.error('Application shutdown incomplete: %s', message)
        raise ApplicationShutdownError(message)
