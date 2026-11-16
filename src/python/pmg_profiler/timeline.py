"""The run over time: bounded timeline points and memory observations."""
from __future__ import annotations

# In-memory timeline bound. The report shows <= 300 points; keeping 300-600 evenly spaced ones
# makes the profiler's own memory constant instead of growing (and being charged to user lines).
TIMELINE_CAP = 600


class Timeline:
    def __init__(self):
        self.points: list[tuple[float, float, float]] = []
        # The main thread's stack key at each kept timeline point (None: no user frame), thinned with the
        # timeline, so the report can split the run into phases by what the program was doing.
        self.keys: list[tuple | None] = []
        self._every = 1    # keep one sample in this many; doubles each time the cap is hit
        self._tick = 0
        self.memory_observations = []
        self._memory_every = 1
        self._memory_tick = 0

    def observe(self, point):
        self._memory_tick += 1
        if self._memory_tick % self._memory_every:
            return
        self.memory_observations.append(point)
        if len(self.memory_observations) >= TIMELINE_CAP:
            self.memory_observations = self.memory_observations[::2]
            self._memory_every *= 2

    def record(self, point, key=None):
        """Keep at most TIMELINE_CAP evenly spaced samples: when full, drop every other point
        and halve the keep rate, so memory stays constant for any run length. key: the main
        thread's stack key at this point, kept in a parallel list."""
        self._tick += 1
        if self._tick < self._every:
            return
        self._tick = 0
        self.points.append(point)
        self.keys.append(key)
        if len(self.points) >= TIMELINE_CAP:
            # The dropped last point was one old stride after the new last point, so the next
            # kept sample is one old stride away: that keeps the spacing exactly even.
            del self.points[1::2]
            del self.keys[1::2]
            self._tick = self._every
            self._every *= 2

    def close(self, point):
        """Add the point after user code exits; it is never thinned."""
        self.points.append(point)
        self.keys.append(None)
