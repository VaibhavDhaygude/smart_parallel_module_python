import inspect
import os
import time


def _get_available_memory_mb():
    """Return available system memory in MB. Uses /proc/meminfo on Linux,
    falls back to psutil if available, otherwise returns None."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024  # kB → MB
    except (OSError, ValueError):
        pass
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 * 1024)
    except ImportError:
        pass
    return None


def _measure_peak_memory_mb(func, sample_item):
    """Estimate per-call memory footprint by measuring RSS before and after
    running func on a single item. Returns delta in MB."""
    try:
        import resource
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # kB on Linux
        func(sample_item)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        delta_kb = max(after - before, 0)
        return delta_kb / 1024  # → MB
    except Exception:
        return 0.0


def _heuristic_classify(func):
    """Lightweight source-code scan. Used only as a fallback when runtime
    profiling is disabled or sample_data is empty."""
    try:
        source = inspect.getsource(func).lower()
    except (OSError, TypeError):
        source = ""
    code_names = " ".join(
        getattr(getattr(func, "__code__", None), "co_names", ())
    ).lower()
    scan_text = f"{source} {code_names}"

    io_markers = (
        "sleep", "open", "read", "write", "requests.", "urlopen",
        "socket.", "recv", "send", "await ", "aiohttp", "httpx",
        "subprocess", "paramiko",
    )
    cpu_markers = (
        "for ", "while ", "range", "sum", "sorted", "numpy", "np.",
        "pandas", "pd.", "math.", "pow", "sqrt",
    )

    has_io = any(m in scan_text for m in io_markers)
    has_cpu = any(m in scan_text for m in cpu_markers)

    if has_io and has_cpu:
        return "mixed"
    if has_io:
        return "io"
    if has_cpu:
        return "cpu"
    return "unknown"


def _runtime_classify(func, sample_data):
    """Actually execute the function on sample data and measure CPU vs wall
    time to determine workload type — the most reliable method.

    Returns (workload_type, per_call_memory_mb).
    """
    per_call_mb = _measure_peak_memory_mb(func, sample_data[0])

    start = time.time()
    cpu_start = time.process_time()

    for item in sample_data:
        func(item)

    wall_time = time.time() - start
    cpu_time = time.process_time() - cpu_start

    if wall_time == 0:
        return "cpu", per_call_mb

    ratio = cpu_time / wall_time
    if ratio > 0.8:
        return "cpu", per_call_mb
    if ratio < 0.3:
        return "io", per_call_mb
    return "mixed", per_call_mb


def classify_workload(func, sample_data, allow_execute=True):
    """Classify a function's workload type.

    Returns a dict with:
      - "workload": "cpu" | "io" | "mixed" | "unknown"
      - "per_call_mb": estimated memory per call (0.0 if not measured)
      - "available_mb": system available memory (None if unknown)

    Strategy:
      1. If allow_execute is True and sample_data is available, **measure**
         the actual CPU-to-wall-time ratio + memory (most accurate).
      2. Otherwise, fall back to the heuristic source-code scan.
    """
    available_mb = _get_available_memory_mb()

    if allow_execute and sample_data:
        workload, per_call_mb = _runtime_classify(func, sample_data)
    else:
        workload = _heuristic_classify(func)
        per_call_mb = 0.0

    return {
        "workload": workload,
        "per_call_mb": per_call_mb,
        "available_mb": available_mb,
    }
