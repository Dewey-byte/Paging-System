"""Run blocking work (TTS synthesis, decoding, exports) off the GUI thread."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_active: set["_Task"] = set()


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # delivered to the GUI thread
            exc.traceback_text = traceback.format_exc()
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None) -> None:
    task = _Task(fn)
    _active.add(task)

    def finish_ok(result):
        _active.discard(task)
        if on_done:
            on_done(result)

    def finish_err(exc):
        _active.discard(task)
        if on_error:
            on_error(exc)

    task.signals.done.connect(finish_ok)
    task.signals.failed.connect(finish_err)
    QThreadPool.globalInstance().start(task)
