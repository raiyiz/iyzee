from textual.widgets import Button, Input, Static, TextArea

from iyzee.tui.navigation.policy import NavigationPolicy


def test_editable_widgets_derive_insert_mode_from_focus() -> None:
    assert NavigationPolicy.is_insert(Input())
    assert NavigationPolicy.is_insert(TextArea())


def test_non_editable_widgets_stay_in_normal_mode() -> None:
    assert not NavigationPolicy.is_insert(Button("Run"))
    assert not NavigationPolicy.is_insert(Static("status"))


def test_custom_editable_widgets_can_opt_in() -> None:
    widget = Static("value")
    widget.is_editable = True
    assert NavigationPolicy.is_insert(widget)
