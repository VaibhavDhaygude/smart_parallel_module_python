from multiprocessing import Pool

def run_process(func, data, workers):
    with Pool(processes=workers) as pool:
        return pool.map(func, data)
