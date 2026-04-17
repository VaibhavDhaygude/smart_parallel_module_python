import os

# Minimum MB of free memory to reserve for the OS and other processes.
_MEMORY_RESERVE_MB = 512
# Assumed per-process overhead when we can't measure it.
_DEFAULT_PROCESS_MB = 50
# Assumed per-thread overhead when we can't measure it.
_DEFAULT_THREAD_MB = 10


def _safe_worker_count(ideal, per_worker_mb, available_mb, executor_type):
    """Clamp *ideal* worker count so total estimated memory stays within
    available memory minus a safety reserve."""
    if available_mb is None:
        # Can't determine memory — use a conservative fallback.
        return max(1, min(ideal, 4 if executor_type == "process" else 16))

    usable_mb = max(available_mb - _MEMORY_RESERVE_MB, 0)
    if per_worker_mb <= 0:
        per_worker_mb = (
            _DEFAULT_PROCESS_MB if executor_type == "process" else _DEFAULT_THREAD_MB
        )

    safe = int(usable_mb / per_worker_mb) if per_worker_mb > 0 else ideal
    return max(1, min(ideal, safe))


def choose_strategy(classification):
    """Pick executor type and worker count from a classification dict.

    *classification* can be:
      - a dict returned by classify_workload (has 'workload', 'per_call_mb',
        'available_mb')
      - a plain string ("cpu", "io", etc.) for backward compat / explicit mode
    """
    cpu_count = os.cpu_count() or 2

    # Support plain string for explicit mode= usage.
    if isinstance(classification, str):
        classification = {
            "workload": classification,
            "per_call_mb": 0.0,
            "available_mb": None,
        }

    workload = classification["workload"]
    per_call_mb = classification.get("per_call_mb", 0.0)
    available_mb = classification.get("available_mb")

    if workload in ("cpu", "mixed"):
        ideal = cpu_count
        # Processes duplicate the interpreter → higher memory per worker.
        mem_per_worker = per_call_mb + _DEFAULT_PROCESS_MB
        workers = _safe_worker_count(ideal, mem_per_worker, available_mb, "process")
        return {"type": "process", "workers": workers}

    # io / unknown → threads
    ideal = min(32, cpu_count * 5)
    mem_per_worker = per_call_mb + _DEFAULT_THREAD_MB
    workers = _safe_worker_count(ideal, mem_per_worker, available_mb, "thread")
    return {"type": "thread", "workers": workers}
