from __future__ import annotations

from typing import Any

from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService

from .mesh_execution_service import MeshExecutionService as _BaseMeshExecutionService


class MeshExecutionService(_BaseMeshExecutionService):
    """Mesh worker with an explicit manual-retry contract.

    Physical probe failures must pause for an operator decision.  The legacy
    ``max_point_retries`` argument is accepted for compatibility but ignored so
    callers cannot accidentally re-enable automatic physical retries.
    """

    def __init__(
        self,
        physical_map_service: PhysicalMapService,
        *,
        max_point_retries: int = 0,
        point_watchdog_timeout_s: float | None = None,
        point_watchdog_poll_s: float = 0.05,
        point_watchdog_grace_s: float = 0.2,
    ) -> None:
        del max_point_retries
        super().__init__(
            physical_map_service,
            max_point_retries=0,
            point_watchdog_timeout_s=point_watchdog_timeout_s,
            point_watchdog_poll_s=point_watchdog_poll_s,
            point_watchdog_grace_s=point_watchdog_grace_s,
        )
        self.max_point_retries = 0
