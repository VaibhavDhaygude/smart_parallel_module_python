import time

import pytest

from smart_parallel import smart_map
from smart_parallel.profiler import classify_workload
def _boom(x):
    raise ValueError("boom")


def _cpu_like(x):
    total = 0
    for i in range(100):
        total += i * x
    return total


def _io_like(x):
    time.sleep(0.001)
    return x

def test_basic():
    data = [1, 2, 3]
    result = smart_map(lambda x: x + 1, data)
    assert result == [2, 3, 4]


def test_runtime_workload_classification():
    # Runtime profiling is now the default (allow_execute=True)
    # classify_workload returns a dict
    result = classify_workload(_cpu_like, [1, 2, 3])
    assert result["workload"] == "cpu"
    result = classify_workload(_io_like, [1, 2, 3])
    assert result["workload"] == "io"


def test_auto_mode_default_does_not_execute_profile_sample():
    calls = []

    def tracked(x):
        calls.append(x)
        return x + 1

    data = list(range(30))
    result = smart_map(tracked, data, mode="auto", sample_size=10, profile=False)

    assert result == [x + 1 for x in data]
    assert len(calls) == len(data)


def test_process_mode_falls_back_for_unpicklable_callable():
    data = list(range(30))
    result = smart_map(lambda x: x + 1, data, mode="cpu")
    assert result == [x + 1 for x in data]


def test_non_pickling_errors_are_raised():
    data = list(range(30))
    with pytest.raises(ValueError, match="boom"):
        smart_map(_boom, data, mode="cpu")
