# smart-parallel

Auto-optimized parallel execution for Python.

## Usage

from smart_parallel import smart_map

def work(x):
    return x * x

results = smart_map(work, range(1000))

# Optional: execute sample profiling (off by default to avoid duplicate side effects)
results = smart_map(work, range(1000), profile=True)
