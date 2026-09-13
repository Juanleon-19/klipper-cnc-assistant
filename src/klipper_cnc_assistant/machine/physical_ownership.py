"""Logical ownership. Locks protect bookkeeping only, never IO or callbacks."""
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from uuid import uuid4


class OwnershipError(RuntimeError):
    pass


class OwnerKind(StrEnum):
    IDLE = 'IDLE'
    RUNTIME_MOTION = 'RUNTIME_MOTION'
    MESH = 'MESH'
    JOB_EXECUTION = 'JOB_EXECUTION'
    RECOVERY = 'RECOVERY'


@dataclass(frozen=True)
class PhysicalPermit:
    session: str
    root: str
    token: str
    owner_kind: OwnerKind
    owner_id: str


class PhysicalMachineCoordinator:
    def __init__(self, *, uncertain: bool = False):
        self._lock = RLock()
        self._session = uuid4().hex
        self._root: PhysicalPermit | None = None
        self._permits: dict[str, PhysicalPermit] = {}
        self._dispatching: set[str] = set()
        self._cancelled = False
        self._uncertain_dispatch = None
        self._recovery = uncertain
        self._producer_active = False
        self._revision = 0
        self._reason = 'Estado remoto no reconciliado.' if uncertain else None

    @property
    def session(self):
        with self._lock:
            return self._session

    def acquire(self, owner_kind, owner_id, *, expected_revision=None):
        kind = OwnerKind(owner_kind)
        if kind in {OwnerKind.IDLE, OwnerKind.RECOVERY} or not owner_id:
            raise OwnershipError('Owner inválido.')
        with self._lock:
            if expected_revision is not None and expected_revision != self._revision:
                raise OwnershipError("La autoridad física cambió antes de aplicar configuración.")
            if self._root is not None or self._recovery:
                raise OwnershipError('La máquina ya tiene ownership físico o recuperación pendiente.')
            token = uuid4().hex
            permit = PhysicalPermit(self._session, token, token, kind, str(owner_id))
            self._revision += 1
            self._producer_active = True
            self._root = permit
            self._permits[token] = permit
            self._cancelled = False
            return permit

    def _validate(self, permit, *, allow_blocked=False):
        if not isinstance(permit, PhysicalPermit) or permit.session != self._session or self._permits.get(permit.token) != permit or self._root is None or permit.root != self._root.root:
            raise OwnershipError('Lease físico inválido o de sesión anterior.')
        if not allow_blocked and (self._recovery or self._cancelled or not self._producer_active):
            raise OwnershipError('Ownership cancelado o en recuperación; emisión bloqueada.')

    def validate(self, permit):
        with self._lock:
            self._validate(permit)

    def delegate(self, permit, owner_id):
        with self._lock:
            self._validate(permit)
            if permit != self._root:
                raise OwnershipError('Solo la raíz puede delegar.')
            child = PhysicalPermit(self._session, permit.root, uuid4().hex, permit.owner_kind, str(owner_id))
            self._revision += 1
            self._permits[child.token] = child
            return child

    def begin_dispatch(self, permit):
        with self._lock:
            self._validate(permit)
            if self._dispatching:
                raise OwnershipError('Ya hay una emisión física en curso.')
            self._revision += 1
            self._dispatching.add(permit.token)

    def end_dispatch(self, permit):
        with self._lock:
            self._revision += 1
            self._dispatching.discard(permit.token)

    def request_cancel(self, permit=None):
        with self._lock:
            if permit is not None:
                self._validate(permit, allow_blocked=True)
            if self._root is not None:
                self._revision += 1
                self._uncertain_dispatch = None
                self._cancelled = True
                self._recovery = True
                self._reason = 'Cancelación pendiente de quiescencia.'

    def enter_recovery(self, reason, permit=None):
        with self._lock:
            if permit is not None:
                self._validate(permit, allow_blocked=True)
            self._revision += 1
            self._uncertain_dispatch = None
            self._recovery = True
            self._reason = str(reason)

    def release(self, permit, *, quiescent=False):
        with self._lock:
            self._validate(permit, allow_blocked=True)
            if permit.token in self._dispatching:
                raise OwnershipError('Una emisión conserva el lease.')
            if permit == self._root:
                if not quiescent or len(self._permits) != 1 or self._recovery:
                    raise OwnershipError('No se puede liberar sin quiescencia y cierre de hijos/recuperación.')
                self._root = None
                self._producer_active = False
                self._cancelled = False
            self._revision += 1
            del self._permits[permit.token]

    def retire(self, permit):
        """Root workflow declares that it will produce no more physical actions."""
        with self._lock:
            self._validate(permit, allow_blocked=True)
            if permit != self._root:
                raise OwnershipError('Solo la raíz puede cerrar su productor.')
            self._producer_active = False
            self._revision += 1
            self._recovery = True
            self._reason = 'Productor cerrado; esperando evidencia física de quiescencia.'

    def dispatch_failed(self, permit):
        with self._lock:
            self._validate(permit, allow_blocked=True)
            # Never overwrite a cancellation or a different recovery cause.
            if not self._recovery:
                self._recovery = True
                self._uncertain_dispatch = permit.token
                self._reason = 'Resultado de emisión incierto.'
                self._revision += 1

    def confirm_motion(self, permit):
        """Only the failed emission's exact target observation resolves its timeout."""
        with self._lock:
            self._validate(permit, allow_blocked=True)
            if self._uncertain_dispatch == permit.token and not self._cancelled and self._producer_active:
                self._recovery = False
                self._uncertain_dispatch = None
                self._reason = None
                self._revision += 1

    def reconcile_quiescent(self, *, expected_session, quiescent, expected_revision=None):
        """Caller must obtain fresh remote idle evidence AND stop local producers."""
        with self._lock:
            if not quiescent or expected_session != self._session or (expected_revision is not None and expected_revision != self._revision) or self._producer_active or self._dispatching or (self._root and len(self._permits) > 1):
                raise OwnershipError('Quiescencia no demostrada o productores aún activos.')
            self._permits.clear()
            self._root = None
            self._cancelled = False
            self._recovery = False
            self._reason = None
            self._session = uuid4().hex
            self._revision += 1

    def snapshot(self):
        with self._lock:
            kind = OwnerKind.RECOVERY if self._recovery else (self._root.owner_kind if self._root else OwnerKind.IDLE)
            return {'kind': kind.value, 'owner_id': None if self._root is None else self._root.owner_id,
                    'active': kind != OwnerKind.IDLE, 'recovery_pending': self._recovery,
                    'cancel_requested': self._cancelled, 'reason': self._reason,
                    'session': self._session, 'revision': self._revision, 'producer_active': self._producer_active, 'dispatching': len(self._dispatching), 'children': max(0, len(self._permits) - 1)}
