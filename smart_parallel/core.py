import pickle
from .profiler import classify_workload
from .strategy import choose_strategy
from .executors.thread_exec import run_threaded
from .executors.process_exec import run_process

_strategy_cache = {}


def _is_picklable(value):
    try:
        pickle.dumps(value)
        return True
    except Exception:
        return False


def _is_pickling_error(exc):
    msg = str(exc).lower()
    markers = ("pickle", "pickling", "can't pickle", "cannot pickle")
    return any(marker in msg for marker in markers)


def smart_map(func, data, mode="auto", sample_size=10, profile=True):
    data = list(data)

    if len(data) < 20:
        return [func(x) for x in data]

    if mode == "auto":
        if func in _strategy_cache:
            strategy = _strategy_cache[func]
        else:
            sample_data = data[:sample_size]
            classification = classify_workload(func, sample_data, allow_execute=profile)
            strategy = choose_strategy(classification)
            _strategy_cache[func] = strategy
    else:
        strategy = choose_strategy(mode)

    if strategy["type"] == "thread":
        return run_threaded(func, data, strategy["workers"])

    if not _is_picklable(func):
        return run_threaded(func, data, strategy["workers"])

    try:
        return run_process(func, data, strategy["workers"])
    except Exception as exc:
        if _is_pickling_error(exc):
            return run_threaded(func, data, strategy["workers"])
        raise
