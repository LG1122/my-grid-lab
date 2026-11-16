"""
Worker routines for Monte Carlo cascade simulation.
Provides isolated run_trial() and batched simulate_trials_chunk() for multiprocessing.Pool.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Set, Tuple

from .topology import GridTopologySnapshot


def run_trial(
    snapshot: GridTopologySnapshot,
    k_fault: int = 1,
    rng: Optional[random.Random] = None,
    max_steps: int = 25,
    blackout_threshold: float = 0.05,
) -> Tuple[bool, int, float, List[int]]:
    """
    Executes a single Monte Carlo trial on the grid topology.
    
    1. Selects k initial branch outages using weighted random sampling proportional to flow / capacity.
    2. Simulates cascading failure propagation: redistributes line flows to incident active lines.
    3. Checks relay overloads (flow > capacity) iteratively until stabilization or max_steps.
    4. Computes lost load (DNS proxy) for isolated buses and determines blackout condition.
    
    Returns:
        (blackout: bool, cascade_trips: int, dns_proxy: float, failed_branches: List[int])
    """
    if rng is None:
        rng = random.Random()

    n_branches = len(snapshot.branches)
    active_branches = [True] * n_branches
    flows = [abs(br.flow) for br in snapshot.branches]
    capacities = [br.capacity for br in snapshot.branches]

    # Pre-calculate sampling weights for N-k shock
    weights = []
    for i in range(n_branches):
        ratio = flows[i] / max(0.001, capacities[i])
        weights.append(max(0.0001, ratio))

    # Select k distinct branches for initial outage
    k = min(max(1, k_fault), n_branches)
    try:
        # Use random.choices with replacement-elimination for exact k unique picks
        chosen_initial: Set[int] = set()
        attempts = 0
        while len(chosen_initial) < k and attempts < 100:
            picked = rng.choices(range(n_branches), weights=weights, k=k - len(chosen_initial))
            chosen_initial.update(picked)
            attempts += 1
        if len(chosen_initial) < k:
            all_indices = list(range(n_branches))
            rng.shuffle(all_indices)
            for idx in all_indices:
                chosen_initial.add(idx)
                if len(chosen_initial) == k:
                    break
    except Exception:
        chosen_initial = set(rng.sample(range(n_branches), k))

    # Apply initial shock
    failed_branches: List[int] = list(chosen_initial)
    for b_idx in chosen_initial:
        active_branches[b_idx] = False

    new_failed = list(chosen_initial)
    cascade_trips = 0
    lost_load = 0.0
    isolated_buses: Set[int] = set()

    # Bus-to-active-branches tracking
    bus_active_branches: Dict[int, Set[int]] = {
        bus_id: set(br_ids) for bus_id, br_ids in snapshot.adjacency.items()
    }
    for b_idx in chosen_initial:
        br = snapshot.branches[b_idx]
        bus_active_branches[br.from_bus].discard(b_idx)
        bus_active_branches[br.to_bus].discard(b_idx)

    # Cascade redistribution loop
    step = 0
    while new_failed and step < max_steps:
        step += 1
        tripping_now = new_failed
        new_failed = []

        for b_idx in tripping_now:
            br = snapshot.branches[b_idx]
            tripped_flow = flows[b_idx]

            # Redistribute tripped flow between both endpoint buses
            for bus_id in (br.from_bus, br.to_bus):
                surviving = bus_active_branches.get(bus_id, set())
                if not surviving:
                    if bus_id not in isolated_buses:
                        isolated_buses.add(bus_id)
                        # Add lost load (bus index is bus_id - 1)
                        if 1 <= bus_id <= len(snapshot.buses):
                            bus_load = snapshot.buses[bus_id - 1].load
                            lost_load += abs(bus_load)
                else:
                    # Allocate flow to surviving incident lines based on free capacity
                    surviving_list = list(surviving)
                    reserves = [max(0.0, capacities[surv] - flows[surv]) for surv in surviving_list]
                    total_res = sum(reserves)
                    share_flow = tripped_flow * 0.5  # half to each terminal node

                    if total_res > 0.001:
                        for surv, res in zip(surviving_list, reserves):
                            flows[surv] += share_flow * (res / total_res)
                    else:
                        equal_share = share_flow / len(surviving_list)
                        for surv in surviving_list:
                            flows[surv] += equal_share

        # Overload evaluation step: check if newly distributed flow exceeds capacity
        for i in range(n_branches):
            if active_branches[i] and flows[i] > capacities[i]:
                active_branches[i] = False
                cascade_trips += 1
                failed_branches.append(i)
                new_failed.append(i)
                br = snapshot.branches[i]
                bus_active_branches[br.from_bus].discard(i)
                bus_active_branches[br.to_bus].discard(i)

    # Calculate DNS proxy and blackout flag
    total_load = max(1.0, snapshot.total_load)
    dns_proxy = min(1.0, lost_load / total_load)
    blackout = bool(dns_proxy > blackout_threshold)

    return blackout, cascade_trips, dns_proxy, failed_branches


def simulate_trials_chunk(chunk_args: Tuple[Any, ...]) -> Dict[str, Any]:
    """
    Top-level worker function designed for multiprocessing.Pool.map.
    Unpacks arguments, executes an isolated batch of trials, and returns
    reduced scalar aggregates to eliminate IPC serialization bottlenecks.
    
    Args:
        chunk_args: (chunk_id, n_trials, snapshot, k_fault, worker_seed, blackout_threshold)
    """
    chunk_id, n_trials, snapshot, k_fault, worker_seed, blackout_threshold = chunk_args
    rng = random.Random(worker_seed)

    n_branches = len(snapshot.branches)
    branch_trips = [0] * n_branches

    blackouts_count = 0
    cascade_trips_sum = 0
    dns_sum = 0.0
    dns_max = 0.0

    for _ in range(n_trials):
        blackout, trips, dns, failed_list = run_trial(
            snapshot=snapshot,
            k_fault=k_fault,
            rng=rng,
            blackout_threshold=blackout_threshold,
        )
        if blackout:
            blackouts_count += 1
        cascade_trips_sum += trips
        dns_sum += dns
        if dns > dns_max:
            dns_max = dns

        for b_idx in failed_list:
            if 0 <= b_idx < n_branches:
                branch_trips[b_idx] += 1

    return {
        "chunk_id": chunk_id,
        "trials_executed": n_trials,
        "blackouts_count": blackouts_count,
        "cascade_trips_sum": cascade_trips_sum,
        "dns_sum": dns_sum,
        "dns_max": dns_max,
        "branch_trips": branch_trips,
    }

