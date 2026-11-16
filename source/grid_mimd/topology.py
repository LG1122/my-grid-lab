"""
Topology loader and snapshot builder for IEEE-118 Power Grid.
Supports loading from PowerGraph .mat dataset or fallback synthetic IEEE-118 layout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class GridBranch:
    id: int
    from_bus: int
    to_bus: int
    flow: float
    capacity: float


@dataclass(frozen=True)
class GridBus:
    id: int
    load: float


@dataclass
class GridTopologySnapshot:
    buses: List[GridBus]
    branches: List[GridBranch]
    adjacency: Dict[int, List[int]]  # bus_id -> list of incident branch ids
    total_load: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "buses": [{"id": b.id, "load": b.load} for b in self.buses],
            "branches": [
                {
                    "id": br.id,
                    "from_bus": br.from_bus,
                    "to_bus": br.to_bus,
                    "flow": br.flow,
                    "capacity": br.capacity,
                }
                for br in self.branches
            ],
            "adjacency": {k: list(v) for k, v in self.adjacency.items()},
            "total_load": self.total_load,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GridTopologySnapshot:
        buses = [GridBus(id=b["id"], load=float(b["load"])) for b in data["buses"]]
        branches = [
            GridBranch(
                id=int(br["id"]),
                from_bus=int(br["from_bus"]),
                to_bus=int(br["to_bus"]),
                flow=float(br["flow"]),
                capacity=float(br["capacity"]),
            )
            for br in data["branches"]
        ]
        adjacency = {int(k): [int(x) for x in v] for k, v in data["adjacency"].items()}
        return cls(
            buses=buses,
            branches=branches,
            adjacency=adjacency,
            total_load=float(data["total_load"]),
        )


def build_topology_snapshot(
    blist: Any,
    branch_features: Any,
    bus_features: Any,
) -> GridTopologySnapshot:
    """
    Builds a clean, immutable-friendly GridTopologySnapshot from raw arrays.
    Per IEEE-118 PowerGraph specifications:
      - Line ratings <= 0 (transformers / busbars without explicit limit)
        are assigned capacity = max(1.0, 2.0 * abs(flow)).
    """
    n_branches = len(blist)
    branches: List[GridBranch] = []
    adjacency: Dict[int, List[int]] = {}

    for i in range(n_branches):
        from_bus = int(blist[i][0])
        to_bus = int(blist[i][1])
        initial_flow = float(branch_features[i][0])
        raw_capacity = float(branch_features[i][3])

        # Protection heuristic for negative or zero normalized capacities
        if raw_capacity <= 0.0 or not (raw_capacity == raw_capacity):  # nan check
            capacity = max(1.0, 2.0 * abs(initial_flow))
        else:
            capacity = max(raw_capacity, abs(initial_flow) * 1.05)

        branch = GridBranch(
            id=i,
            from_bus=from_bus,
            to_bus=to_bus,
            flow=initial_flow,
            capacity=capacity,
        )
        branches.append(branch)

        adjacency.setdefault(from_bus, []).append(i)
        adjacency.setdefault(to_bus, []).append(i)

    n_buses = len(bus_features)
    buses: List[GridBus] = []
    total_load = 0.0

    for j in range(n_buses):
        bus_id = j + 1
        p_net = float(bus_features[j][0])  # Active power net
        buses.append(GridBus(id=bus_id, load=p_net))
        # Total consumption load across system (negative P_net indicates demand)
        if p_net < 0:
            total_load += abs(p_net)
        else:
            total_load += p_net * 0.5  # include baseline load component

    if total_load <= 0.0:
        total_load = max(100.0, float(n_buses) * 10.0)

    return GridTopologySnapshot(
        buses=buses,
        branches=branches,
        adjacency=adjacency,
        total_load=total_load,
    )


def generate_synthetic_ieee118_snapshot(seed: int = 42) -> GridTopologySnapshot:
    """
    Generates a deterministic synthetic IEEE-118 topology (118 buses, 186 branches)
    to enable testing and benchmarks when external .mat files are not present.
    """
    import random

    rng = random.Random(seed)
    n_buses = 118
    n_branches = 186

    # Build spanning tree first to ensure connectedness
    edges = set()
    adjacency: Dict[int, List[int]] = {b: [] for b in range(1, n_buses + 1)}

    for i in range(2, n_buses + 1):
        target = rng.randint(max(1, i - 15), i - 1)
        edge = (min(target, i), max(target, i))
        edges.add(edge)

    # Add remaining branches up to 186
    while len(edges) < n_branches:
        u = rng.randint(1, n_buses)
        v = rng.randint(1, n_buses)
        if u != v:
            edge = (min(u, v), max(u, v))
            edges.add(edge)

    edge_list = sorted(list(edges))
    branches: List[GridBranch] = []
    for idx, (u, v) in enumerate(edge_list):
        flow = rng.uniform(0.1, 0.8)
        cap = flow * rng.uniform(1.2, 2.5)
        branches.append(GridBranch(id=idx, from_bus=u, to_bus=v, flow=flow, capacity=cap))
        adjacency[u].append(idx)
        adjacency[v].append(idx)

    buses: List[GridBus] = []
    total_load = 0.0
    for b in range(1, n_buses + 1):
        load = rng.uniform(-1.5, 2.0)
        buses.append(GridBus(id=b, load=load))
        if load < 0:
            total_load += abs(load)
        else:
            total_load += load * 0.4

    if total_load <= 0:
        total_load = 1000.0

    return GridTopologySnapshot(
        buses=buses,
        branches=branches,
        adjacency=adjacency,
        total_load=total_load,
    )


def load_scenario(
    data_dir: Optional[str] = None,
    sample_idx: int = 0,
) -> GridTopologySnapshot:
    """
    Loads topology for a specific scenario index s.
    Attempts to use load_powergraph_cascades from cloud_grid if available;
    falls back to synthetic IEEE-118 topology if dataset files are absent.
    """
    candidates = []
    if data_dir:
        candidates.append(Path(data_dir))
    candidates.extend(
        [
            Path("Datasets"),
            Path("../cloud_grid/Datasets"),
            Path("../../cloud_grid/Datasets"),
            Path(__file__).resolve().parents[3] / "cloud_grid" / "Datasets",
            Path.home() / "Downloads" / "dataset",
        ]
    )

    found_raw_dir = None
    for cand in candidates:
        if (cand / "dataset_cascades" / "ieee118" / "ieee118" / "raw" / "blist.mat").exists():
            found_raw_dir = cand / "dataset_cascades" / "ieee118" / "ieee118" / "raw"
            break
        if (cand / "blist.mat").exists():
            found_raw_dir = cand
            break

    if found_raw_dir is not None:
        try:
            # Dynamically import load_powergraph_cascades
            scripts_dir = Path(__file__).resolve().parents[3] / "cloud_grid" / "source" / "dataset_scripts"
            if scripts_dir.exists() and str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))

            from load_powergraph_cascades import load_powergraph_cascades  # type: ignore

            pg = load_powergraph_cascades(
                found_raw_dir,
                sample_index=slice(sample_idx, sample_idx + 1),
                load_large=True,
            )
            if pg.n_samples > 0 and pg.Ef.size > 0:
                return build_topology_snapshot(pg.blist, pg.Ef[0], pg.Bf[0])
        except Exception:
            pass

    # Fallback to deterministic IEEE-118 synthetic snapshot
    return generate_synthetic_ieee118_snapshot(seed=42 + sample_idx)

