"""Host-wide flock for cooperating Assistant processes, unrelated to data_dir.

External Moonraker clients do not honor this lock. Never unlink the lock file.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import stat


class PhysicalProcessLockError(RuntimeError):
    pass


class PhysicalProcessLock:
    def __init__(self, machine_id='default-cnc', *, directory=Path('/tmp')):
        digest = hashlib.sha256(machine_id.encode()).hexdigest()
        self.path = Path(directory) / f'cnc-assistant-physical-{digest}.lock'
        self._fd = None
        self._pid = None

    @property
    def held(self):
        return self._fd is not None and self._pid == os.getpid()

    def acquire(self):
        if self.held:
            return
        fd = None
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PhysicalProcessLockError('El lock no es un archivo regular.')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, PhysicalProcessLockError) as error:
            if fd is not None:
                os.close(fd)
            raise PhysicalProcessLockError('No se pudo adquirir acceso físico exclusivo.') from error
        self._fd, self._pid = fd, os.getpid()

    def require_held(self):
        if not self.held:
            raise PhysicalProcessLockError('La instancia no posee el lock físico del proceso.')

    def close(self):
        if self._fd is not None:
            # close also works in a forked child without unlocking the parent.
            os.close(self._fd)
            self._fd = self._pid = None
