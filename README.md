# `smart_parallel` — Auto-Parallelization for Python

**Stop guessing whether to use threads or processes.** `smart_parallel` profiles your function, measures your system resources, and picks the fastest parallel strategy automatically.

```bash
pip install smart_parallel
```

```python
from smart_parallel import smart_map

results = smart_map(your_function, your_data)
```

---

## API Reference

```python
smart_map(func, data, mode="auto", sample_size=10, profile=True)
```

| Parameter     | Type     | Default      | Description |
|---------------|----------|--------------|-------------|
| `func`        | callable | *(required)* | The function to apply to each item in `data` |
| `data`        | iterable | *(required)* | The collection of items to process in parallel |
| `mode`        | str      | `"auto"`     | Execution strategy — see **Modes** below |
| `sample_size` | int      | `10`         | How many items from `data` to profile when in auto mode |
| `profile`     | bool     | `True`       | Whether to actually *run* sample items for profiling, or use source-code heuristics only |

**Returns:** A list of results, in the same order as `data`.

---

## Modes

| Mode | What it does |
|------|-------------|
| `"auto"` | **(Default)** Profiles your function to decide between threads and processes. This is the smart path — see **How It Works** below |
| `"thread"` / `"io"` | Forces **multithreading** using `ThreadPoolExecutor`. Best for I/O-bound work (network calls, file I/O, `sleep`) |
| `"process"` / `"cpu"` | Forces **multiprocessing** using `multiprocessing.Pool`. Best for CPU-bound work (math, loops, data crunching) |

---

## What is `sample_size`?

When `mode="auto"`, the library doesn't just guess — it **actually runs your function** on a small sample of your data to measure performance characteristics. `sample_size` controls how many items it tests.

```python
# Profile using first 10 items (default)
results = smart_map(my_func, data)

# Profile using first 25 items (more accurate, slightly slower startup)
results = smart_map(my_func, data, sample_size=25)

# Profile using first 3 items (faster startup, less accurate)
results = smart_map(my_func, data, sample_size=3)
```

**Trade-off:** Larger sample = more accurate classification, but longer startup. For most workloads, the default of 10 is fine. Increase it if your function has variable behavior across different inputs.

---

## What is the Profiler?

The profiler (`profile=True`) is the brain of auto mode. It answers the question: **"Is this function spending time *computing* or *waiting*?"**

There are **two profiling methods**:

### 1. Runtime Profiling (`profile=True`, default)

The most accurate method. It actually runs your function on `sample_size` items and measures:

- **CPU time** — how much time the CPU was actively working (`time.process_time()`)
- **Wall time** — how much real-world time elapsed (`time.time()`)
- **Memory usage** — how much RAM each call consumes (via `resource.getrusage`)

Then it calculates the **CPU-to-wall-time ratio**:

| Ratio | Classification | Meaning |
|-------|---------------|---------|
| **> 0.8** | `"cpu"` | Function is CPU-bound — the CPU is busy the whole time (e.g. math, loops) |
| **< 0.3** | `"io"` | Function is I/O-bound — the CPU is mostly idle, waiting for something (e.g. network, disk) |
| **0.3 – 0.8** | `"mixed"` | A mix of both — treated as CPU-bound to be safe |

**Example:** A function that takes 1 second wall time but only 0.05s CPU time has ratio 0.05 → classified as I/O-bound → uses threads.

### 2. Heuristic Profiling (`profile=False`)

A lightweight fallback that **doesn't run your function at all**. Instead, it scans the function's **source code** looking for keywords:

- **I/O markers:** `sleep`, `open`, `read`, `write`, `requests.`, `urlopen`, `socket.`, `aiohttp`, `httpx`, `subprocess`, etc.
- **CPU markers:** `for`, `while`, `range`, `sum`, `sorted`, `numpy`, `pandas`, `math.`, `pow`, `sqrt`, etc.

Use this when you don't want the profiler to execute your function (e.g. it has side effects):

```python
results = smart_map(send_email, emails, profile=False)
```

---

## How It Actually Works (Step by Step)

Here's the complete flow when you call `smart_map(func, data)`:

```
smart_map(func, data)
    │
    ├─ Step 1: Is data small? (<20 items)
    │    └─ YES → Run sequentially, return results. No parallelism overhead.
    │
    ├─ Step 2: Check mode
    │    ├─ mode="thread"/"io" → Skip to Step 5 (threads)
    │    ├─ mode="process"/"cpu" → Skip to Step 5 (processes)
    │    └─ mode="auto" → Continue to Step 3
    │
    ├─ Step 3: Is this function already cached?
    │    ├─ YES → Reuse cached strategy, skip to Step 5
    │    └─ NO → Continue to Step 4
    │
    ├─ Step 4: PROFILE the function
    │    ├─ Take first `sample_size` items from data
    │    ├─ If profile=True:
    │    │    ├─ Measure memory usage on 1st item (RSS delta)
    │    │    ├─ Run func on all sample items
    │    │    ├─ Measure CPU time vs wall time
    │    │    └─ Classify: cpu (ratio>0.8), io (ratio<0.3), mixed
    │    └─ If profile=False:
    │         └─ Scan source code for I/O and CPU keywords
    │
    │    Then CHOOSE STRATEGY based on classification:
    │    ├─ "cpu" or "mixed" → multiprocessing
    │    │    └─ workers = cpu_count (clamped by available RAM)
    │    └─ "io" or "unknown" → threading
    │         └─ workers = min(32, cpu_count × 5) (clamped by available RAM)
    │
    │    Cache the strategy for this function.
    │
    ├─ Step 5: EXECUTE
    │    ├─ If threading → ThreadPoolExecutor(max_workers=N)
    │    └─ If multiprocessing:
    │         ├─ Check: can func be pickled?
    │         │    └─ NO → Fall back to threads (lambdas, closures)
    │         ├─ Run with multiprocessing.Pool(processes=N)
    │         └─ If pickle error at runtime → Fall back to threads
    │
    └─ Return: ordered list of results
```

---

## Memory-Aware Worker Scaling

The library doesn't just pick an arbitrary number of workers. It checks your **available system RAM** and calculates:

```
usable_memory = available_RAM - 512 MB (safety reserve)
max_workers = usable_memory / (per_call_memory + overhead)
```

| Executor | Assumed overhead per worker |
|----------|---------------------------|
| Process  | 50 MB (each process duplicates the Python interpreter) |
| Thread   | 10 MB (threads share memory, much lighter) |

If it can measure your function's actual memory usage (via the profiler), it adds that to the overhead for more accurate scaling. This prevents your system from running out of memory with large worker pools.

**Fallback when RAM can't be detected:** max 4 processes or 16 threads.

---

## Strategy Caching

After profiling a function once, the result is cached in memory:

```python
# First call: profiles heavy_math, picks strategy, caches it
results1 = smart_map(heavy_math, batch_1)

# Second call: skips profiling entirely, reuses cached strategy
results2 = smart_map(heavy_math, batch_2)
```

Cache is per-function and lives for the duration of the Python process.

---

## Automatic Pickle Fallback

Multiprocessing requires data to be serializable (picklable). Some things can't be pickled — lambdas, closures, local functions. `smart_parallel` handles this gracefully:

1. Before using multiprocessing, it checks if `func` can be pickled
2. If not → silently falls back to threads
3. If a pickle error happens at runtime → catches it and retries with threads

```python
# This works fine — lambda can't be pickled, auto-falls back to threads
results = smart_map(lambda x: x * 2, range(100), mode="process")
```

---

## Examples

**Auto mode (let the library decide):**
```python
from smart_parallel import smart_map

def heavy_math(n):
    return sum(i * i for i in range(n))

results = smart_map(heavy_math, range(100, 500))
```

**Force threads for I/O work:**
```python
import requests
from smart_parallel import smart_map

def fetch(url):
    return requests.get(url).status_code

results = smart_map(fetch, urls, mode="thread")
```

**Force processes for CPU work:**
```python
from smart_parallel import smart_map

def crunch(n):
    return sum(i ** 2 for i in range(n))

results = smart_map(crunch, range(1000), mode="process")
```

**Skip runtime profiling (no side effects during profiling):**
```python
results = smart_map(send_email, email_list, profile=False)
```

**Larger sample for variable workloads:**
```python
results = smart_map(process_file, files, sample_size=25)
```

---

## Requirements

- Python 3.8+
- `psutil` (installed automatically)
