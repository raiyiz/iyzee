"""Shared base class and mechanics for interactive TUI pages.

The page base owns plumbing that is identical across pages rather than any
instrument-specific behavior:

* scrolling/focus behavior, so tall pages remain reachable without stealing
  initial focus;
* narrowing Widget.app to the concrete IyzeeApp used by this TUI;
* the safe worker-to-UI callback used by blocking background operations;
* common labeled-field construction and validation error/marker handling.

Keeping these here means screens stay focused on their own controls and
domain operations. The helpers are intentionally small: they remove repeated
UI mechanics without hiding the actual behavior each screen performs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Label

if TYPE_CHECKING:
    from ..app import IyzeeApp

log = logging.getLogger("iyzee.tui")

T = TypeVar("T")


class FieldError(ValueError):
    """A form field failed validation.

    field_id identifies the offending Input so the caller can mark and focus
    the same control while still presenting a normal ValueError to code that
    only cares that validation failed.
    """

    def __init__(self, field_id: str, message: str) -> None:
        super().__init__(message)
        self.field_id = field_id


def _field(label: str, widget: Widget, *, id: str | None = None) -> Vertical:
    """Build the label-over-widget container used by form grids.

    Grouping the label and input into one container lets app.tcss reflow
    forms without ever separating a field's label from its control.
    """
    return Vertical(Label(label), widget, classes="field", id=id)


class Page(VerticalScroll, can_focus=False):
    """Scrollable base and shared plumbing for all interactive pages.

    A plain Vertical hides whatever overflows it, which is how controls ended
    up below the fold with no way to reach them. VerticalScroll makes the
    same content reachable on short terminals; the horizontal axis is
    enabled in app.tcss.

    can_focus=False is deliberate. VerticalScroll is focusable by default,
    and Textual's auto-focus would otherwise pick the page container itself,
    stealing focus from the widget a page actually wants focused (Connect's
    instrument table, for example). Scrolling still works through the mouse
    wheel, scrollbars, and bubbled PageUp/PageDown bindings.

    The remaining helpers are UI mechanics shared by multiple pages:

    * iyzee_app narrows Textual's generic Widget.app type once, in one place.
    * _ui safely schedules a worker's UI callback and treats a page being
      torn down or replaced as an ordinary race, not an unhandled worker
      exception.
    * _read turns ordinary ValueError parser failures into the page-shared
      FieldError that also identifies which input to fix.
    * _flag_invalid gives that field a visible -invalid marker and moves
      focus to it; editing any input clears its marker again via
      on_input_changed.
    """

    @property
    def iyzee_app(self) -> IyzeeApp:
        """The concrete app instance used by every page in this TUI."""
        return cast("IyzeeApp", self.app)

    def _ui(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Run a worker-completion callback on the Textual UI thread.

        A background operation can finish after its page has been replaced or
        unmounted. Textual propagates callback exceptions back to the worker
        thread, so a stale-widget race would otherwise appear as an unrelated
        worker failure. Logging and skipping that update is the safe fallback;
        the operation itself has already finished.
        """
        try:
            self.app.call_from_thread(callback, *args, **kwargs)
        except Exception:
            log.exception(
                "%s: UI update from worker thread failed",
                self.__class__.__name__,
            )

    def _flag_invalid(self, field_id: str) -> None:
        """Mark field_id invalid and focus it so it can be corrected."""
        for widget in self.query("Input.-invalid"):
            widget.remove_class("-invalid")
        widget = self.query_one(f"#{field_id}", Input)
        widget.add_class("-invalid")
        widget.focus()

    def _read(
        self, field_id: str, parse: Callable[..., T], label: str, **kwargs: object
    ) -> T:
        """Parse an Input and attach its field id to ValueError."""
        try:
            return parse(self.query_one(f"#{field_id}", Input).value, label, **kwargs)
        except ValueError as exc:
            raise FieldError(field_id, str(exc)) from exc

    def on_input_changed(self, event: Input.Changed) -> None:
        """Editing a previously invalid input clears its error marker."""
        event.input.remove_class("-invalid")
