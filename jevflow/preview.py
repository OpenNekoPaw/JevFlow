"""Configuration inspection only: no execution, file writes or provider clients."""
import copy
import hashlib
from pathlib import Path
from threading import Lock

from .schema import parse_flow, require
from .topology import graph_data


MAX_CONFIG_BYTES = 1024 * 1024


def preview_data(source):
    require(len(source.encode("utf-8")) <= MAX_CONFIG_BYTES, "YAML exceeds the 1 MiB preview limit")
    flow = parse_flow(source)
    graph = graph_data(flow)
    return {"name": flow.get("title", flow["name"]), "revision": graph["revision"],
            "flowHash": graph["flowHash"], "graph": graph, "trace": None, "replay": None,
            "finished": False, "status": "preview"}


class ConfigurationPreview:
    """Poll only the startup-selected path and retain its last valid snapshot."""
    def __init__(self, path=None):
        # Do not resolve symlinks: replacing a watched path must be observed too.
        self.path = Path(path).expanduser().absolute() if path is not None else None
        self.lock = Lock()
        self.fingerprint = None
        self.data = None
        self.error = None

    def snapshot(self):
        with self.lock:
            if self.path is not None:
                try:
                    with self.path.open("rb") as stream:
                        raw = stream.read(MAX_CONFIG_BYTES + 1)
                    require(len(raw) <= MAX_CONFIG_BYTES, "YAML exceeds the 1 MiB preview limit")
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest != self.fingerprint:
                        self.fingerprint = digest
                        try:
                            candidate = preview_data(raw.decode("utf-8-sig"))
                        except (ValueError, TypeError, RecursionError) as exc:
                            self.error = str(exc)
                        else:
                            self.data, self.error = candidate, None
                except (OSError, ValueError) as exc:
                    self.error = str(exc)
                    self.fingerprint = None  # Retry after the file becomes readable again.
            return {"configured": self.path is not None, "source": str(self.path) if self.path else None,
                    "data": copy.deepcopy(self.data), "error": self.error,
                    "stale": self.error is not None and self.data is not None}
