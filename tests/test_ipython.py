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
    class Handle:
        def __init__(self, device):
            self.device = device

    device = object()
    handles = {"mxa": Handle(device), "scope": Handle("scope")}
    namespace = namespace_from_handles(handles)
    assert namespace["mx"] is device
    assert namespace["scope"] == "scope"
    assert namespace["handles"] is handles
