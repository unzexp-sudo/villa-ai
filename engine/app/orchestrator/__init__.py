"""M4 ORCHESTRATOR — merge, clash detection / resolution, performance, run.

Public API consumed by M6 (``start_run`` / ``get_run``), plus the pipeline
stages for direct use and tests.
"""

from .run import RunContext, start_run, get_run, execute_run
from .merge import merge_layers, all_elements, element_count
from .clash import (
    detect_clashes, element_distance_mm, INTRA_SYSTEM_CLEARANCE_MM, MAX_CLASHES,
)
from .resolver import resolve_clashes
from .performance import run_performance_loop

__all__ = [
    "RunContext", "start_run", "get_run", "execute_run",
    "merge_layers", "all_elements", "element_count",
    "detect_clashes", "element_distance_mm", "INTRA_SYSTEM_CLEARANCE_MM",
    "MAX_CLASHES", "resolve_clashes", "run_performance_loop",
]
