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
