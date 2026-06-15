"""Small shared accounting helpers."""

from dataclasses import dataclass


@dataclass
class Rate:
    """Two outcome counters and the share that went well.

    `good`/`bad` are deliberately neutral so the same accounting serves both
    cache hits/misses and LLM call successes/failures.
    """

    good: int = 0
    bad: int = 0

    @property
    def total(self) -> int:
        return self.good + self.bad

    def rate(self) -> float:
        return self.good / self.total if self.total else 0.0
