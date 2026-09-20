"""Shared errors and cooperative cancellation; no dependency on the engine."""


class FlowError(ValueError):
    pass


class FlowCancelled(FlowError):
    pass


def check_cancelled(event):
    if event is not None and event.is_set():
        raise FlowCancelled("Flow cancelled")
