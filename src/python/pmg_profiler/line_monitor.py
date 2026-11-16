"""Optional Python 3.12+ LINE events: execution coverage, not timing or memory."""
from __future__ import annotations

import os
import sys

from .common import UserFiles


class LineMonitor:
    def __init__(self, requested: str, users: UserFiles):
        self.requested = requested
        self.users = users
        self.reason = None
        self._tool = None
        self.enabled = False
        self.events: dict[tuple[str, int], int] = {}
        self._files: dict[str, str | None] = {}   # co_filename -> absolute user path, or None
        self._disable = None                      # sys.monitoring.DISABLE once enabled
        self.dropped = 0

    def enable(self):
        """Optional execution evidence; sampled timing still uses _current_frames()."""
        if self.requested != 'lines':
            return
        mon = getattr(sys, 'monitoring', None)
        if mon is None:
            self.reason = 'requires Python 3.12+'
            return
        tool = mon.PROFILER_ID
        if mon.get_tool(tool) is not None:
            self.reason = 'profiler monitoring tool ID is already in use'
            return
        try:
            self._disable = mon.DISABLE
            mon.use_tool_id(tool, 'python-memory-guardian')
            self._tool = tool
            mon.register_callback(tool, mon.events.LINE, self.on_line_event)
            mon.set_events(tool, mon.events.LINE)
            self.enabled = True
        except Exception:
            self.reason = 'could not enable execution-event monitoring'
            self.disable()

    def on_line_event(self, code, line):
        """Count user line events. Library and stdlib lines return DISABLE, which turns LINE
        events off at that location for this tool only, so they cost one callback each, not one
        per execution. User paths are resolved once per file name."""
        filename = code.co_filename
        path = self._files.get(filename, False)
        if path is False:
            path = self._files[filename] = (
                os.path.abspath(filename) if self.users.is_user(filename) else None)
        if path is None:
            return self._disable
        loc = (path, line)
        if loc in self.events:
            self.events[loc] += 1
        elif len(self.events) < 50_000:
            self.events[loc] = 1
        else:
            self.dropped += 1

    def disable(self):
        tool = self._tool
        if tool is None:
            return
        self._tool = None
        mon = sys.monitoring
        try:
            mon.set_events(tool, 0)
        finally:
            try:
                mon.register_callback(tool, mon.events.LINE, None)
            finally:
                mon.free_tool_id(tool)
