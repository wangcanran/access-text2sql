"""Shared serial API pacing; never adds its own retries."""
import time


class RateGate:
    def __init__(self, interval=2.0, *, clock=time.monotonic, sleep=time.sleep):
        if interval < 0:
            raise ValueError('API interval must be nonnegative')
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.next_start = 0.0

    def wrap(self, create):
        def paced(**kwargs):
            delay = max(0.0, self.next_start - self.clock())
            if delay:
                self.sleep(delay)
            self.next_start = self.clock() + self.interval
            try:
                return create(**kwargs)
            except Exception as exc:
                if getattr(exc, 'status_code', None) == 429:
                    headers = getattr(getattr(exc, 'response', None), 'headers', {})
                    try:
                        backoff = max(10.0, float(headers.get('retry-after', 10)))
                    except (TypeError, ValueError):
                        backoff = 10.0
                    self.next_start = max(self.next_start, self.clock() + backoff)
                raise
        return paced
