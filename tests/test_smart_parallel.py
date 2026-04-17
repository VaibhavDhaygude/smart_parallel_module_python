"""
Comprehensive tests for the smart_parallel module.

Covers:
  - smart_map with auto / cpu / io / thread modes
  - Small-data fast path (< 20 items)
  - Heuristic and profiling-based workload classification
  - Strategy selection for cpu / io / unknown workloads
  - Pickling fallback (lambda → threads instead of processes)
  - Non-pickling errors propagate correctly
  - Thread and process executors directly
  - Strategy cache behaviour
"""

import math
import time

import pytest

from smart_parallel import smart_map
from smart_parallel.core import _is_picklable, _is_pickling_error, _strategy_cache
from smart_parallel.profiler import classify_workload, _heuristic_classify, _runtime_classify
from smart_parallel.strategy import choose_strategy, _safe_worker_count
from smart_parallel.executors.thread_exec import run_threaded
from smart_parallel.executors.process_exec import run_process


# ---------------------------------------------------------------------------
# Helper functions (must be module-level so they are picklable)
# ---------------------------------------------------------------------------

def _square(x):
    return x * x


def _cpu_heavy(x):
    """CPU-bound: tight loop."""
    total = 0
    for i in range(500):
        total += i * x
    return total


def _io_heavy(x):
    """IO-bound: sleeps."""
    time.sleep(0.001)
    return x * 2


def _pure(x):
    return x + 1


def _raise_value(x):
    raise ValueError("intentional error")


def _raise_runtime(x):
    raise RuntimeError("something broke")


# ---------------------------------------------------------------------------
# Tests: small-data fast path
# ---------------------------------------------------------------------------

class TestSmallDataFastPath:
    def test_returns_correct_results_for_tiny_list(self):
        assert smart_map(_pure, [1, 2, 3]) == [2, 3, 4]

    def test_empty_list(self):
        assert smart_map(_pure, []) == []

    def test_single_element(self):
        assert smart_map(_square, [5]) == [25]

    def test_exactly_19_elements(self):
        data = list(range(19))
        assert smart_map(_square, data) == [x * x for x in data]


# ---------------------------------------------------------------------------
# Tests: auto mode
# ---------------------------------------------------------------------------

class TestAutoMode:
    def test_auto_cpu_workload(self):
        data = list(range(50))
        result = smart_map(_cpu_heavy, data, mode="auto")
        assert result == [_cpu_heavy(x) for x in data]

    def test_auto_io_workload(self):
        data = list(range(30))
        result = smart_map(_io_heavy, data, mode="auto")
        assert result == [x * 2 for x in data]

    def test_auto_with_profile_enabled(self):
        data = list(range(30))
        result = smart_map(_pure, data, mode="auto", profile=True)
        assert result == [x + 1 for x in data]

    def test_auto_profiles_sample_by_default(self):
        calls = []

        def tracked(x):
            calls.append(x)
            return x

        data = list(range(30))
        _strategy_cache.clear()
        smart_map(tracked, data, mode="auto")
        # tracked is called during:
        #   memory measurement (1) + runtime profiling (10) + actual map (30) = 41
        assert len(calls) == len(data) + 10 + 1

    def test_auto_skips_profiling_when_disabled(self):
        calls = []

        def tracked(x):
            calls.append(x)
            return x

        data = list(range(30))
        _strategy_cache.clear()
        smart_map(tracked, data, mode="auto", profile=False)
        # tracked is called only during actual map, not during profiling
        assert len(calls) == len(data)


# ---------------------------------------------------------------------------
# Tests: explicit modes
# ---------------------------------------------------------------------------

class TestExplicitModes:
    def test_cpu_mode(self):
        data = list(range(30))
        result = smart_map(_square, data, mode="cpu")
        assert result == [x * x for x in data]

    def test_io_mode(self):
        data = list(range(30))
        result = smart_map(_pure, data, mode="io")
        assert result == [x + 1 for x in data]

    def test_thread_mode_via_io(self):
        data = list(range(30))
        result = smart_map(_pure, data, mode="io")
        assert result == [x + 1 for x in data]


# ---------------------------------------------------------------------------
# Tests: pickling fallback
# ---------------------------------------------------------------------------

class TestPicklingFallback:
    def test_lambda_falls_back_to_threads(self):
        data = list(range(30))
        result = smart_map(lambda x: x + 10, data, mode="cpu")
        assert result == [x + 10 for x in data]

    def test_closure_falls_back_to_threads(self):
        offset = 42

        def add_offset(x):
            return x + offset

        data = list(range(30))
        result = smart_map(add_offset, data, mode="cpu")
        assert result == [x + offset for x in data]

    def test_is_picklable_module_level_func(self):
        assert _is_picklable(_square) is True

    def test_is_picklable_lambda(self):
        assert _is_picklable(lambda x: x) is False


# ---------------------------------------------------------------------------
# Tests: error propagation
# ---------------------------------------------------------------------------

class TestErrorPropagation:
    def test_non_pickling_error_raised(self):
        data = list(range(30))
        with pytest.raises(ValueError, match="intentional error"):
            smart_map(_raise_value, data, mode="cpu")

    def test_runtime_error_raised(self):
        data = list(range(30))
        with pytest.raises(RuntimeError, match="something broke"):
            smart_map(_raise_runtime, data, mode="cpu")

    def test_is_pickling_error_detection(self):
        assert _is_pickling_error(Exception("can't pickle local object")) is True
        assert _is_pickling_error(Exception("pickling failed")) is True
        assert _is_pickling_error(Exception("some other error")) is False


# ---------------------------------------------------------------------------
# Tests: profiler / heuristic classification
# ---------------------------------------------------------------------------

class TestProfiler:
    # -- heuristic (fallback) tests --
    def test_cpu_heuristic(self):
        assert _heuristic_classify(_cpu_heavy) == "cpu"

    def test_io_heuristic(self):
        assert _heuristic_classify(_io_heavy) == "io"

    def test_unknown_heuristic_for_simple_func(self):
        def simple(x):
            return x
        assert _heuristic_classify(simple) == "unknown"

    # -- runtime classify tests --
    def test_runtime_cpu(self):
        workload, mem = _runtime_classify(_cpu_heavy, list(range(20)))
        assert workload == "cpu"
        assert isinstance(mem, float)

    def test_runtime_io(self):
        workload, mem = _runtime_classify(_io_heavy, list(range(10)))
        assert workload == "io"
        assert isinstance(mem, float)

    def test_runtime_fast_func(self):
        workload, mem = _runtime_classify(_pure, list(range(10)))
        assert workload in ("cpu", "mixed")

    # -- classify_workload (runtime-first, returns dict) tests --
    def test_classify_defaults_to_runtime(self):
        result = classify_workload(_cpu_heavy, list(range(20)))
        assert result["workload"] == "cpu"
        assert "per_call_mb" in result
        assert "available_mb" in result

        result = classify_workload(_io_heavy, list(range(10)))
        assert result["workload"] == "io"

    def test_classify_falls_back_to_heuristic_when_disabled(self):
        result = classify_workload(_cpu_heavy, [1, 2], allow_execute=False)
        assert result["workload"] == "cpu"
        assert result["per_call_mb"] == 0.0

        result = classify_workload(_io_heavy, [1, 2], allow_execute=False)
        assert result["workload"] == "io"

    def test_classify_falls_back_to_heuristic_with_empty_sample(self):
        result = classify_workload(_cpu_heavy, [])
        assert result["workload"] == "cpu"

    def test_classify_builtin(self):
        result = classify_workload(abs, [1, -2, 3], allow_execute=True)
        assert result["workload"] in ("cpu", "io", "mixed")


# ---------------------------------------------------------------------------
# Tests: strategy selection
# ---------------------------------------------------------------------------

class TestStrategy:
    def test_cpu_strategy(self):
        s = choose_strategy("cpu")
        assert s["type"] == "process"
        assert s["workers"] >= 1

    def test_io_strategy(self):
        s = choose_strategy("io")
        assert s["type"] == "thread"
        assert s["workers"] >= 1

    def test_unknown_defaults_to_thread(self):
        s = choose_strategy("unknown")
        assert s["type"] == "thread"

    def test_mixed_strategy(self):
        s = choose_strategy("mixed")
        assert s["type"] == "process"
        assert s["workers"] >= 1

    def test_dict_classification_input(self):
        c = {"workload": "cpu", "per_call_mb": 10.0, "available_mb": 8000.0}
        s = choose_strategy(c)
        assert s["type"] == "process"
        assert s["workers"] >= 1

    def test_memory_limits_process_workers(self):
        # Only 600 MB available, 512 reserved → 88 usable.
        # per_call_mb=10 + 50 overhead = 60 MB/worker → max 1 worker.
        c = {"workload": "cpu", "per_call_mb": 10.0, "available_mb": 600.0}
        s = choose_strategy(c)
        assert s["workers"] == 1

    def test_memory_limits_thread_workers(self):
        # Only 530 MB available, 512 reserved → 18 usable.
        # per_call_mb=1 + 10 overhead = 11 MB/worker → max 1 worker.
        c = {"workload": "io", "per_call_mb": 1.0, "available_mb": 530.0}
        s = choose_strategy(c)
        assert s["workers"] == 1

    def test_plenty_of_memory_does_not_reduce_workers(self):
        c = {"workload": "cpu", "per_call_mb": 1.0, "available_mb": 32000.0}
        s = choose_strategy(c)
        import os
        assert s["workers"] == (os.cpu_count() or 2)

    def test_safe_worker_count_unknown_memory(self):
        # When available_mb is None, fall back to conservative defaults.
        assert _safe_worker_count(16, 100.0, None, "process") <= 4
        assert _safe_worker_count(32, 10.0, None, "thread") <= 16


# ---------------------------------------------------------------------------
# Tests: executors directly
# ---------------------------------------------------------------------------

class TestExecutors:
    def test_run_threaded(self):
        result = run_threaded(_square, [1, 2, 3, 4], workers=2)
        assert result == [1, 4, 9, 16]

    def test_run_process(self):
        result = run_process(_square, [1, 2, 3, 4], workers=2)
        assert result == [1, 4, 9, 16]

    def test_threaded_preserves_order(self):
        data = list(range(100))
        result = run_threaded(_pure, data, workers=4)
        assert result == [x + 1 for x in data]

    def test_process_preserves_order(self):
        data = list(range(100))
        result = run_process(_pure, data, workers=4)
        assert result == [x + 1 for x in data]


# ---------------------------------------------------------------------------
# Tests: strategy cache
# ---------------------------------------------------------------------------

class TestStrategyCache:
    def test_cache_populated_after_auto(self):
        _strategy_cache.clear()
        data = list(range(30))
        smart_map(_cpu_heavy, data, mode="auto")
        assert _cpu_heavy in _strategy_cache

    def test_cache_reused_on_second_call(self):
        _strategy_cache.clear()
        data = list(range(30))
        smart_map(_cpu_heavy, data, mode="auto")
        cached = _strategy_cache[_cpu_heavy]
        smart_map(_cpu_heavy, data, mode="auto")
        assert _strategy_cache[_cpu_heavy] is cached


# ---------------------------------------------------------------------------
# Tests: data type variety
# ---------------------------------------------------------------------------

class TestDataVariety:
    def test_string_data(self):
        data = ["hello", "world", "foo"] * 10
        result = smart_map(str.upper, data, mode="io")
        assert result == [s.upper() for s in data]

    def test_float_data(self):
        data = [float(i) for i in range(30)]
        result = smart_map(math.sqrt, data, mode="cpu")
        assert result == [math.sqrt(x) for x in data]

    def test_generator_input_converted(self):
        gen = (x for x in range(30))
        result = smart_map(_square, gen, mode="io")
        assert result == [x * x for x in range(30)]
