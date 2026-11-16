"""
MIMD Power Grid Cascade Simulation package.
Stage 1: Multi-core CPU parallelization using multiprocessing.Pool.
"""

from .topology import (
    GridBranch,
    GridBus,
    GridTopologySnapshot,
    build_topology_snapshot,
    load_scenario,
)
from .worker import run_trial, simulate_trials_chunk
from .engine import (
    GridSimulationEngine,
    get_shared_pool,
    shutdown_shared_pool,
)
from .benchmark import run_headless_benchmark

__all__ = [
    "GridBranch",
    "GridBus",
    "GridTopologySnapshot",
    "build_topology_snapshot",
    "load_scenario",
    "run_trial",
    "simulate_trials_chunk",
    "GridSimulationEngine",
    "get_shared_pool",
    "shutdown_shared_pool",
    "run_headless_benchmark",
]

