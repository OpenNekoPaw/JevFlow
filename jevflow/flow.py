"""Validated in-memory updates; each run gets its own flow snapshot."""

import copy
from pathlib import Path
from threading import RLock

from .core import _run_snapshot
from .schema import _read_flow, validate_flow


class Flow:
    def __init__(self, definition):
        self._lock = RLock()
        self._path = None
        self.update(definition)

    @classmethod
    def from_file(cls, path):
        instance = cls(_read_flow(path))
        instance._path = Path(path).resolve()
        return instance

    @property
    def definition(self):
        with self._lock:
            return copy.deepcopy(self._definition)

    def update(self, definition):
        candidate = copy.deepcopy(definition)
        candidate = validate_flow(candidate)
        with self._lock:
            self._definition = candidate

    def reload(self):
        if self._path is None:
            raise ValueError("reload requires Flow.from_file; use update for an in-memory flow")
        self.update(_read_flow(self._path))

    def run(self, inputs, client, trace=None, *, deadline_unix_ms=None, cancel_event=None, observer=None):
        return _run_snapshot(self.definition, inputs, client, trace=trace,
                             deadline_unix_ms=deadline_unix_ms, cancel_event=cancel_event, observer=observer)
