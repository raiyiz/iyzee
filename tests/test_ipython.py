from iyzee.tui.ipython import IyzeeIPython, namespace_from_handles


def test_ipython_executes_against_live_namespace() -> None:
    marker = object()
    shell = IyzeeIPython({"instrument": marker})
    output = shell.execute("instrument")
    assert output.success
    assert "object at" in output.stdout


def test_ipython_preserves_variables_between_cells() -> None:
    shell = IyzeeIPython()
    first = shell.execute("measurement = 21")
    second = shell.execute("measurement * 2")
    assert first.success
    assert second.success
    assert "42" in second.stdout


def test_namespace_from_handles_exposes_live_objects() -> None:
    class MxaHandle:
        def __init__(self, device):
            self.device = device

    class ScopeHandle:
        def __init__(self, scope):
            self.scope = scope

    device = object()
    handles = {"mxa": MxaHandle(device), "scope": ScopeHandle("scope")}
    namespace = namespace_from_handles(handles)
    assert namespace["mx"] is device
    assert namespace["scope"] == "scope"
    assert namespace["handles"] is handles


def test_namespace_refresh_removes_disconnected_live_objects() -> None:
    shell = IyzeeIPython({"mx": "closed-device", "measurement": 21})
    shell.update_namespace({"handles": {}})

    assert "mx" not in shell.shell.user_ns
    assert shell.shell.user_ns["measurement"] == 21
    assert shell.shell.user_ns["handles"] == {}


def test_history_is_current_session_only_deduped_and_ordered() -> None:
    shell = IyzeeIPython()
    for src in ["x = 1", "y = 2", "y = 2", "z = x + y"]:
        shell.execute(src)

    assert shell.history == ["x = 1", "y = 2", "z = x + y"]


def test_history_does_not_leak_across_shell_instances() -> None:
    """A fresh IyzeeIPython (e.g. after an app restart) should not surface
    another instance's input, even though IPython persists history to a
    shared sqlite file on disk by default."""
    first = IyzeeIPython()
    first.execute("secret = 1")
    assert first.history == ["secret = 1"]

    second = IyzeeIPython()
    assert second.history == []


def test_locked_proxy_serializes_concurrent_calls() -> None:
    import threading
    import time

    from iyzee.tui.instruments import LockedProxy

    class SlowDevice:
        def __init__(self) -> None:
            self.max_concurrent = 0
            self._active = 0
            self._guard = threading.Lock()

        def do_thing(self) -> None:
            with self._guard:
                self._active += 1
                self.max_concurrent = max(self.max_concurrent, self._active)
            time.sleep(0.05)
            with self._guard:
                self._active -= 1

    device = SlowDevice()
    proxy = LockedProxy(device, threading.Lock())

    threads = [threading.Thread(target=proxy.do_thing) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert device.max_concurrent == 1


def test_namespace_from_handles_wraps_devices_when_locks_given() -> None:
    import threading

    from iyzee.tui.instruments import LockedProxy

    class Handle:
        def __init__(self, device):
            self.device = device

    device = object()
    handles = {"mxa": Handle(device)}
    locks = {"mxa": threading.Lock()}
    namespace = namespace_from_handles(handles, locks)
    assert isinstance(namespace["mx"], LockedProxy)
    assert repr(namespace["mx"]) == repr(device)


def test_live_names_covers_results_and_last_run() -> None:
    # Documents the contract update_namespace() relies on: adding a new
    # app-managed console variable is exactly a one-line addition here,
    # nowhere else.
    assert {"results", "last_run"} <= IyzeeIPython.LIVE_NAMES
