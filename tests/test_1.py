import requests
from smart_parallel import smart_map

urls = [
    "https://httpbin.org/get?page=1",
    "https://httpbin.org/get?page=2",
    "https://httpbin.org/get?page=3",
    "https://httpbin.org/get?page=4",
    "https://httpbin.org/get?page=5",
]

def fetch(url):
    resp = requests.get(url, timeout=10)
    return resp.status_code

# Auto-detects IO-bound → uses threads
results = smart_map(fetch, urls)
print("Status codes:", results)