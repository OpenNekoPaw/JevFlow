"""Validated in-memory updates; each run gets its own flow snapshot."""

import copy
from pathlib import Path
from threading import RLock

from .core import load_flow, run_flow, validate_flow


class Flow:
    def __init__(self, definition):
        self._lock = RLock()
        self._path = None
        self.update(definition)

    @classmethod
    def from_file(cls, path):
        instance = cls(load_flow(path))
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
        self.update(load_flow(self._path))

    def run(self, inputs, client, trace=None):
        return run_flow(self.definition, inputs, client, trace=trace)
