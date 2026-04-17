# Smart Parallel Library — Detailed Notes

## Overview

`smart_parallel` is a Python library that **automatically decides** whether to run
your workload using **threads** or **processes**, based on the nature of the
function you pass in. The user calls a single function — `smart_map(func, data)` —
and the library handles everything else.

---

## Project Structure

```
smart_parallel/
├── __init__.py          # Public API — exports smart_map
├── core.py              # Main entry point: smart_map + pickle helpers
├── profiler.py          # Workload classification (heuristic + runtime)
├── strategy.py          # Maps workload type → executor config
└── executors/
    ├── thread_exec.py   # ThreadPoolExecutor wrapper
    └── process_exec.py  # multiprocessing.Pool wrapper
```

---

## How It Works — Step by Step

### 1. Entry Point: `smart_map()` (core.py)

```python
smart_map(func, data, mode="auto", sample_size=10, profile=False)
```

| Parameter     | Purpose |
|---------------|---------|
| `func`        | The function to apply to each item in `data` |
| `data`        | Any iterable (list, generator, etc.) — converted to a list internally |
| `mode`        | `"auto"` (default), `"cpu"`, or `"io"` — forces a specific strategy |
| `sample_size` | How many items to sample for profiling (default 10) |
| `profile`     | If `True`, allows actual execution of sample items to measure CPU vs wall time |

#### Flow:

```
smart_map(func, data)
    │
    ├─ len(data) < 20?  ──YES──▸  Run sequentially (no parallelism overhead)
    │
    ├─ mode == "auto"?
    │   ├─ func in _strategy_cache?  ──YES──▸  Reuse cached strategy
    │   └─ NO ──▸  classify_workload(func, sample) → choose_strategy() → cache it
    │
    ├─ mode == "cpu" / "io"
    │   └─ choose_strategy(mode) directly
    │
    ├─ strategy["type"] == "thread"?  ──▸  run_threaded()
    │
    └─ strategy["type"] == "process"?
        ├─ func is picklable?  ──YES──▸  run_process()
        │                         (if pickle error at runtime → fallback to run_threaded)
        └─ NO ──▸  Fallback to run_threaded()
```

**Key design decisions:**
- **Small data fast path**: Lists under 20 items skip parallelism entirely (overhead
  would exceed the benefit).
- **Strategy caching**: Once a function is classified, the result is stored in a
  module-level dict `_strategy_cache` keyed by the function object. Subsequent calls
  with the same function skip profiling.
- **Pickle safety**: Multiprocessing requires pickling the function across processes.
  Lambdas and closures can't be pickled, so the lib checks picklability *before*
  attempting multiprocessing, and catches pickle errors at runtime as a second safety net.

---

### 2. Workload Classification (profiler.py)

Two classification methods, tried in order:

#### A. Heuristic Classification (`_heuristic_classify`)

Inspects the function **without executing it**:

1. **Gets the source code** via `inspect.getsource(func)` (lowercased).
2. **Gets bytecode names** from `func.__code__.co_names` (the names referenced in
   the function's bytecode — module names, function calls, etc.).
3. **Scans the combined text** for keyword markers:

| Markers found           | Classification |
|-------------------------|---------------|
| `sleep`, `open`, `read`, `write`, `requests.`, `urlopen`, `socket.`, `recv`, `send`, `await` | **"io"** |
| `for`, `while`, `range`, `sum`, `sorted` | **"cpu"** |
| None of the above       | **"unknown"** |

IO markers are checked **first** — if a function does both IO and CPU work, it's
classified as IO (since threads benefit IO-bound work more, and processes add overhead).

#### B. Runtime Profiling (`classify_workload` with `allow_execute=True`)

Only runs if:
- Heuristic returned `"unknown"`, AND
- `profile=True` was passed to `smart_map`

How it works:
1. Runs `func(item)` on each item in the sample data.
2. Measures **wall-clock time** (`time.time()`) and **CPU time** (`time.process_time()`).
3. Computes the ratio: `cpu_time / wall_time`
   - **> 0.7** → the function spends most time on CPU → `"cpu"`
   - **≤ 0.7** → the function is waiting on IO → `"io"`
   - **wall_time == 0** → instant execution → defaults to `"cpu"`

**Why the 0.7 threshold?**
If a function uses 70%+ of wall time doing CPU work, parallelizing across processes
(bypassing the GIL) helps. Below that, threads suffice since the GIL is released
during IO waits.

---

### 3. Strategy Selection (strategy.py)

```python
choose_strategy(workload_type) → {"type": str, "workers": int}
```

| Workload type | Executor type | Worker count |
|---------------|--------------|-------------|
| `"cpu"`       | `"process"`  | `os.cpu_count()` (e.g., 8 on an 8-core machine) |
| `"io"` or anything else | `"thread"` | `min(32, cpu_count * 5)` (e.g., 40 on 8-core, capped at 32) |

**Rationale:**
- **CPU-bound**: One process per core maximizes throughput; more processes would cause
  context-switching overhead.
- **IO-bound**: Many threads are fine because they spend most time waiting. The 5×
  multiplier and cap of 32 balances concurrency against resource usage.

---

### 4. Executors

#### Thread Executor (thread_exec.py)
```python
ThreadPoolExecutor(max_workers=workers).map(func, data)
```
- Uses Python's `concurrent.futures` thread pool.
- Good for IO-bound work (HTTP requests, file reads, sleeps).
- Subject to GIL — won't speed up pure CPU work.

#### Process Executor (process_exec.py)
```python
multiprocessing.Pool(processes=workers).map(func, data)
```
- Spawns separate OS processes, each with its own Python interpreter.
- Bypasses the GIL — true parallelism for CPU-bound work.
- **Requires** the function and data to be picklable (serializable).

---

### 5. Pickle Fallback Mechanism (core.py)

Multiprocessing sends functions and data to worker processes via `pickle`. This
fails for lambdas, closures, and nested functions. The library handles this with
a **two-layer safety net**:

1. **Pre-check**: `_is_picklable(func)` tries `pickle.dumps(func)` before launching
   the process pool. If it fails → falls back to threads immediately.

2. **Runtime catch**: If `pool.map()` raises an exception whose message contains
   pickle-related keywords (`"can't pickle"`, `"pickling"`, etc.) → falls back to
   threads. Any other exception is re-raised to the caller.

---

## Data Flow Diagram

```
User calls: smart_map(func, data)
                │
                ▼
        ┌───────────────┐
        │  data < 20?   │──YES──▸ Sequential [func(x) for x in data]
        └───────┬───────┘
                │ NO
                ▼
        ┌───────────────┐
        │  mode="auto"? │──NO──▸ choose_strategy(mode)
        └───────┬───────┘
                │ YES
                ▼
        ┌───────────────┐
        │  Cached?      │──YES──▸ Use cached strategy
        └───────┬───────┘
                │ NO
                ▼
        ┌───────────────────────┐
        │  _heuristic_classify  │
        │  (scan source code)   │
        └───────┬───────────────┘
                │
          ┌─────┼──────┐
          ▼     ▼      ▼
        "io"  "cpu"  "unknown"
          │     │      │
          │     │      ▼
          │     │  profile=True? ──NO──▸ treat as "unknown" → thread strategy
          │     │      │ YES
          │     │      ▼
          │     │  Runtime profiling
          │     │  (cpu_time / wall_time)
          │     │      │
          │     │  ┌───┴───┐
          │     │  ▼       ▼
          │     │ "cpu"   "io"
          ▼     ▼         ▼
        ┌─────────────────────┐
        │   choose_strategy   │
        └─────────┬───────────┘
                  │
          ┌───────┴────────┐
          ▼                ▼
      "thread"          "process"
          │                │
          ▼                ├─ picklable? ──NO──▸ "thread" fallback
          │                │ YES
     run_threaded     run_process
                       (catch pickle errors → thread fallback)
```

---

## Key Constants & Thresholds

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| Small data cutoff | 20 items | core.py | Skip parallelism for tiny inputs |
| Default sample size | 10 items | core.py | Items used for profiling |
| CPU/IO ratio threshold | 0.7 | profiler.py | Above = CPU, below = IO |
| Max threads | 32 | strategy.py | Cap to prevent resource exhaustion |
| Thread multiplier | 5 × cpu_count | strategy.py | More threads since IO waits release GIL |
| Process workers | cpu_count | strategy.py | One process per core |

---

## Usage Examples

```python
from smart_parallel import smart_map

# Auto mode (default) — library decides threads vs processes
results = smart_map(my_func, my_data)

# Force threading (IO-bound work)
results = smart_map(fetch_url, urls, mode="io")

# Force multiprocessing (CPU-bound work)
results = smart_map(crunch_numbers, numbers, mode="cpu")

# Enable runtime profiling for unknown functions
results = smart_map(mystery_func, data, profile=True)
```
