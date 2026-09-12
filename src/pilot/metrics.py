"""Privacy-safe resource pressure and execution telemetry."""

import time

import psutil


class Resources:
    def __init__(self):
        self.checked = 0
        self.last = {}

    def sample(self):
        if time.monotonic() - self.checked > 2:
            memory = psutil.virtual_memory()
            self.last = {
                "available_memory_mb": int(memory.available / 1024**2),
                "process_rss_mb": int(psutil.Process().memory_info().rss / 1024**2),
                "memory_worker_budget": max(
                    1, min(10, int(memory.available / (128 * 1024**2)))
                ),
            }
            self.checked = time.monotonic()
        return self.last
