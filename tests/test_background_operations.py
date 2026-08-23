import threading
import time

from stadium_reaper_bridge.editor.background_operations import BackgroundOperations


def wait_for_result(operations, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = operations.poll()
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("background operation did not finish")


def test_operation_is_serial_and_result_is_polled_on_caller_thread():
    operations = BackgroundOperations("test-migration")
    caller = threading.get_ident()
    release = threading.Event()
    worker_threads = []

    def work():
        worker_threads.append(threading.get_ident())
        release.wait(1)
        return "verified"

    assert operations.start("build", work)
    assert operations.active
    assert not operations.start("implant", lambda: None)
    release.set()
    result = wait_for_result(operations)
    assert result.name == "build" and result.value == "verified" and result.error is None
    assert worker_threads == [worker_threads[0]] and worker_threads[0] != caller
    assert not operations.active
    operations.close()


def test_worker_error_is_returned_and_close_prevents_new_callbacks():
    operations = BackgroundOperations()

    def fail():
        raise RuntimeError("verification failed")

    assert operations.start("verify", fail)
    result = wait_for_result(operations)
    assert isinstance(result.error, RuntimeError)
    operations.close()
    assert operations.closed
    assert not operations.start("late", lambda: "must not run")
