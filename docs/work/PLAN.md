# План реалізації: Етап 1 (MIMD-симуляція каскадних аварій на CPU)

## 1. Мета та контекст
- **Курс**: Грід-системи та хмарні обчислення
- **Проєкт**: Симуляція стійкості енергосистеми IEEE-118 методом Монте-Карло
- **Етап 1**: Локальне розпаралелювання обчислень на базі процесорних ядер (MIMD, `multiprocessing.Pool`)
- **Базові референси**:
  - Специфікація: `@cloud_grid/docs/n01_power_grid_project.md`
  - Архітектурний шаблон: `@cloud_grid/source/mimd-pc/app.py`
  - Допоміжні скрипти датасету: `@cloud_grid/source/dataset_scripts/load_powergraph_cascades.py`

---

## 2. Узгоджені архітектурні параметри
1. **Завантаження топології**: Базовий робочий режим мережі $s$ (за замовчуванням $s=0$) зчитується один раз у головному процесі через `load_powergraph_cascades` та `build_topology`. У незмінному вигляді (`read-only snapshot`) передається в підзадачі воркерів.
2. **Критерій блек-ауту**: Прапорець `blackout = True`, якщо відносний недовідпуск потужності перевищує 5% (`dns_proxy > 0.05`).
3. **Генерація початкового збурення**: Зважений випадковий відбір $k$ ліній з імовірностями, пропорційними напруженості ліній:
   $$w_i = \frac{|P_{ij}|}{lr_{ij}}$$
   Для ненормованих шин і трансформаторів ($lr_{ij} \le 0$) призначається безпечний резерв `capacity = max(1.0, 2.0 * abs(flow))`.
4. **Вибірка випробувань**: Для локального налагодження та бенчмарку — $N = 10^3 \dots 10^5$ випробувань.

---

## 3. Модульна структура проєкту

```text
my-grid-lab/
├── .agents/
│   └── system.md
├── .gitignore
├── docs/
│   └── work/
│       └── PLAN.md
└── source/
    └── grid_mimd/
        ├── __init__.py
        ├── topology.py    # Завантаження датасету, парсинг матриць, граф IEEE-118
        ├── worker.py      # Атомарний run_trial() та пакетний simulate_trials_chunk()
        ├── engine.py      # GridSimulationEngine, пул процесів, Reduce результатів
        └── benchmark.py   # CLI інтерфейс та збереження benchmark_results_mimd_grid.json
```

### Призначення публічних функцій та класів:

#### `topology.py`
- `load_scenario(data_dir, sample_idx=0)`: зчитування зрізу даних (`blist`, `Bf`, `Ef`, `of_reg`, `of_bi`) без повного завантаження 122 500 станів.
- `build_topology_snapshot(blist, ef_sample, bf_sample)`: формування компактної незмінної структури:
  - Списки гілок: `branches[i] = {id, from_bus, to_bus, flow, capacity}`
  - Списки вузлів: `buses[j] = {id, load}`
  - Списки інцидентності / суміжності: `adj[bus_id] = [branch_ids...]`
  - Сумарне навантаження системи: `total_load` (сума $P_{net}$ споживачів).

#### `worker.py`
- `run_trial(snapshot, k_fault, rng)`: симуляція одного каскадного сценарію:
  1. Копіювання робочого вектора потоків та активності ліній.
  2. Вибір $k$ ліній за зваженим розподілом і переведення їх у стан `FAILED`.
  3. Ітеративний перерозподіл потоку між суміжними лініями за вільним резервом `capacity - flow`.
  4. Фіксація ізольованих вузлів та нарахування недовідпуску $P_{net}$.
  5. Перевірка спрацювання захисту `flow > capacity` та ланцюгове відключення.
  6. Розрахунок `cascade_trips`, `dns_proxy`, `blackout`.
- `simulate_trials_chunk(chunk_args)`: чиста функція модуля для `Pool.map`:
  - Аргументи: `(chunk_id, n_trials, snapshot, k_fault, worker_seed)`.
  - Ініціалізація `random.seed(worker_seed)`.
  - Виконання циклу випробувань з агрегацією сум і гістограми відмов ліній.

#### `engine.py` & `benchmark.py`
- `get_shared_pool(workers)` / `shutdown_shared_pool()`: керування пулом процесів.
- `class GridSimulationEngine`:
  - `run_simulation(n_trials, num_workers, k_fault)`: розподіл $N$ на пачки, запуск `pool.map`, підсумовування метрик.
- `run_headless_benchmark(...)`: виконання прогонів з вимірюванням часу та записом у JSON.
- `main()`: обробка CLI параметрів (`--trials`, `--workers`, `--k`, `--samples`, `--data`, `--out`).

---

## 4. Специфікація `benchmark_results_mimd_grid.json`

```json
{
  "experiment_metadata": {
    "course": "Cloud and Grid Systems",
    "project": "IEEE-118 Power Grid Cascade Simulation",
    "stage": 1,
    "architecture": "MIMD_CPU_Multiprocessing",
    "timestamp_utc": "..."
  },
  "simulation_parameters": {
    "dataset_scenario_index": 0,
    "total_trials": 100000,
    "k_fault": 1,
    "num_workers": 8,
    "chunk_size": 12500,
    "base_seed": 42
  },
  "performance_metrics": {
    "total_wall_time_sec": 0.0,
    "throughput_trials_per_sec": 0.0,
    "speedup_vs_single_core": 0.0,
    "efficiency_percent": 0.0
  },
  "grid_resilience_results": {
    "blackout_probability": 0.0,
    "mean_dns_proxy": 0.0,
    "max_dns_proxy": 0.0,
    "average_cascade_trips": 0.0,
    "top_critical_branches": [
      { "branch_id": 0, "failure_count": 0, "failure_probability": 0.0 }
    ]
  }
}
```

---

## 5. Ризики та технічні рішення
1. **Усунення впливу GIL**: обчислення виносяться у процеси `multiprocessing.Pool`, що забезпечує чесне завантаження всіх ядер процесора.
2. **Оптимізація IPC**: воркер повертає лише 6 скалярних агрегатів та один фіксований числовий масив розміром 186 (гістограма відмов). Жодні об'єкти окремих траєкторій через канали зв'язку не передаються.
3. **Економія RAM**: завантаження датасету обмежене першими зразками (`--samples 0:10`), що потребує менше ніж 50 МБ замість 1.5 ГБ.
4. **Незалежність PRNG**: кожен воркер отримує зерно `worker_seed = base_seed + chunk_idx * 10007`, усуваючи дублювання псевдовипадкових траєкторій.

---

## 6. Чеклист реалізації
- [x] План узгоджено (`APPROVE PLAN`).
- [x] Реалізувати `source/grid_mimd/topology.py`.
- [x] Реалізувати `source/grid_mimd/worker.py` (`run_trial` + `simulate_trials_chunk`).
- [x] Реалізувати `source/grid_mimd/engine.py` та `benchmark.py`.
- [ ] Перевірити однопотоковий режим проти багатопотокового.
- [ ] Сформувати еталонний звіт `benchmark_results_mimd_grid.json`.

