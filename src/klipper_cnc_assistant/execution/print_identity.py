"""Moonraker filenames are exact paths relative to the gcodes root."""
from dataclasses import dataclass

from klipper_cnc_assistant.application.errors import ApplicationError


class PrintIdentityError(ApplicationError):
    pass


@dataclass(frozen=True)
class PrintIdentity:
    filename: str

    def __post_init__(self):
        value = self.filename
        if (not isinstance(value, str) or not value or value != value.strip()
                or '\\' in value or any(ord(c) < 32 for c in value)
                or any(part in {'', '.', '..'} for part in value.split('/'))):
            raise PrintIdentityError('Identidad de archivo Moonraker inválida.')

    @staticmethod
    def require_status(status):
        if (not isinstance(status, dict) or status.get('connected') is not True
                or status.get('klipper_state') != 'ready' or not status.get('state')):
            raise PrintIdentityError('No se pudo identificar la impresión Moonraker.')

    def require_match(self, status, *, states=None):
        self.require_status(status)
        if PrintIdentity(status.get('filename')).filename != self.filename:
            raise PrintIdentityError('El archivo Moonraker no coincide con el esperado por JobRun.')
        if states is not None and status['state'] not in states:
            raise PrintIdentityError('Estado Moonraker incompatible con la acción solicitada.')

    @classmethod
    def require_idle(cls, status):
        cls.require_status(status)
        if (status['state'] not in {'standby', 'complete', 'completed', 'cancelled', 'error'}
                or status.get('is_active', status.get('active')) is not False):
            raise PrintIdentityError('Moonraker no está inactivo; start bloqueado.')
