from concurrent.futures import ThreadPoolExecutor

def run_threaded(func, data, workers):
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(func, data))
