"""
Simulation engine and shared multiprocessing pool manager.
Coordinates chunked dispatch of Monte Carlo trials across CPU cores.
"""

from __future__ import annotations

import time
from multiprocessing import Pool, cpu_count
from typing import Any, Dict, List, Optional

from .topology import GridTopologySnapshot
from .worker import simulate_trials_chunk

SHARED_POOL: Optional[Pool] = None


def get_shared_pool(num_workers: Optional[int] = None) -> Pool:
    """
    Returns a persistent shared multiprocessing.Pool.
    Prevents repeated fork/teardown overhead across benchmark iterations.
    """
    global SHARED_POOL
    if SHARED_POOL is None:
        workers = num_workers or cpu_count()
        SHARED_POOL = Pool(processes=workers)
    return SHARED_POOL


def shutdown_shared_pool(terminate: bool = False) -> None:
    """
    Safely shuts down the shared multiprocessing.Pool.
    """
    global SHARED_POOL
    if SHARED_POOL is not None:
        if terminate:
            SHARED_POOL.terminate()
        else:
            SHARED_POOL.close()
        SHARED_POOL.join()
        SHARED_POOL = None


class GridSimulationEngine:
    """
    Coordinates MIMD Monte Carlo resilience simulation on the grid topology snapshot.
    """

    def __init__(
        self,
        snapshot: GridTopologySnapshot,
        num_workers: Optional[int] = None,
    ) -> None:
        self.snapshot = snapshot
        self.num_workers = max(1, int(num_workers or cpu_count()))

    def run_simulation(
        self,
        n_trials: int = 100000,
        k_fault: int = 1,
        base_seed: int = 42,
        blackout_threshold: float = 0.05,
    ) -> Dict[str, Any]:
        """
        Executes n_trials across parallel worker processes using chunking.
        """
        n_trials = max(1, int(n_trials))
        k_fault = max(1, int(k_fault))

        # Partition trials among workers
        base_chunk = n_trials // self.num_workers
        remainder = n_trials % self.num_workers

        task_args = []
        for i in range(self.num_workers):
            chunk_trials = base_chunk + (1 if i < remainder else 0)
            if chunk_trials <= 0:
                continue
            worker_seed = base_seed + i * 10007
            task_args.append(
                (
                    i,
                    chunk_trials,
                    self.snapshot,
                    k_fault,
                    worker_seed,
                    blackout_threshold,
                )
            )

        start_time = time.perf_counter()

        if self.num_workers == 1 or len(task_args) == 1:
            # Direct execution in single-worker mode without IPC overhead
            results = [simulate_trials_chunk(task_args[0])]
        else:
            pool = get_shared_pool(self.num_workers)
            results = pool.map(simulate_trials_chunk, task_args)

        elapsed_sec = time.perf_counter() - start_time

        # Reduce chunk aggregates
        total_executed = sum(r["trials_executed"] for r in results)
        total_blackouts = sum(r["blackouts_count"] for r in results)
        total_trips = sum(r["cascade_trips_sum"] for r in results)
        total_dns = sum(r["dns_sum"] for r in results)
        overall_max_dns = max(r["dns_max"] for r in results) if results else 0.0

        n_branches = len(self.snapshot.branches)
        aggregated_branch_trips = [0] * n_branches
        for r in results:
            for b_idx, count in enumerate(r["branch_trips"]):
                aggregated_branch_trips[b_idx] += count

        # Rank most vulnerable branches
        branch_ranking = []
        for b_idx, count in enumerate(aggregated_branch_trips):
            if count > 0:
                branch_ranking.append(
                    {
                        "branch_id": b_idx,
                        "failure_count": count,
                        "failure_probability": round(count / total_executed, 5),
                    }
                )
        branch_ranking.sort(key=lambda x: x["failure_count"], reverse=True)
        top_critical = branch_ranking[:10]

        throughput = total_executed / max(0.0001, elapsed_sec)

        return {
            "total_trials": total_executed,
            "k_fault": k_fault,
            "num_workers": self.num_workers,
            "elapsed_sec": round(elapsed_sec, 4),
            "throughput": round(throughput, 1),
            "blackout_probability": round(total_blackouts / total_executed, 5),
            "mean_dns_proxy": round(total_dns / total_executed, 5),
            "max_dns_proxy": round(overall_max_dns, 5),
            "average_cascade_trips": round(total_trips / total_executed, 3),
            "top_critical_branches": top_critical,
        }

