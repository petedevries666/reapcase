"""Display-independent lifecycle for serialized background operations.

Workers never call Tk (or any caller-owned dispatcher).  The UI polls completed
results from its own thread, which makes teardown safe and keeps this class easy
to exercise without a display.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class OperationResult:
    name: str
    value: Any = None
    error: Optional[BaseException] = None


class BackgroundOperations:
    """Run at most one long-running operation on one dedicated worker."""

    def __init__(self, thread_name: str = "stadium-migration"):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name)
        self._completed = Queue()  # type: Queue[OperationResult]
        self._future = None  # type: Optional[Future]
        self._closed = False

    @property
    def active(self) -> bool:
        return self._future is not None

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, name: str, function: Callable[[], Any]) -> bool:
        """Start work, or return False when work is active/the lifecycle is closed."""
        if self._closed or self.active:
            return False
        future = self._executor.submit(function)
        self._future = future

        def completed(done: Future) -> None:
            try:
                result = OperationResult(name, value=done.result())
            except BaseException as exc:  # returned to and rendered by the UI thread
                result = OperationResult(name, error=exc)
            self._completed.put(result)

        future.add_done_callback(completed)
        return True

    def poll(self) -> Optional[OperationResult]:
        """Consume a result from the caller's thread; never invokes callbacks."""
        try:
            result = self._completed.get_nowait()
        except Empty:
            return None
        self._future = None
        return result

    def close(self) -> None:
        """Ignore eventual results and prevent new work without faking cancellation."""
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
