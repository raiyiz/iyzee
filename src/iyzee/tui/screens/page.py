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
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Label, Select

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


def _finite_float(raw: str, field: str) -> float:
    """Parse ``raw`` as a finite float, rejecting NaN and infinities."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _positive_float(raw: str, field: str) -> float:
    """Parse ``raw`` as a finite, strictly positive float."""
    value = _finite_float(raw, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _positive_int(raw: str, field: str, *, maximum: int | None = None) -> int:
    """Parse ``raw`` as a strictly positive integer, optionally bounded."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


class Page(VerticalScroll, can_focus=False):
    """A page that scrolls instead of clipping when its content doesn't fit.

    A plain ``Vertical`` hides whatever overflows it, which is how
    controls ended up below the fold with no way to reach them. This
    scrolls on demand (the horizontal axis is enabled in ``app.tcss``).

    ``can_focus=False`` is deliberate. ``VerticalScroll`` is focusable by
    default, and Textual's auto-focus picks the *first* focusable widget
    on a page — which would become the page container itself, stealing
    focus from the widget the page actually wants focused (Connect's
    instrument table, so that Enter connects). Scrolling doesn't need the
    container to hold focus: the mouse wheel and scrollbars always work,
    Tab/``j``/``k`` scroll the newly focused widget into view, and
    PageUp/PageDown bubble up from any focused child to this container's
    scroll bindings.

    The remaining helpers are UI mechanics shared by multiple pages:

    * ``iyzee_app`` narrows Textual's generic ``Widget.app`` type once, in
      one place, while preserving the concrete ``IyzeeApp`` type for pages.
    * ``_ui`` safely schedules a worker's UI callback. A background
      operation can finish after its page is replaced or unmounted, and
      Textual propagates callback exceptions back to the worker thread;
      logging and skipping that stale update is safer than surfacing an
      unrelated worker failure.
    * ``_read``/``FieldError`` and ``_flag_invalid`` centralize form
      validation: parser failures still identify the offending field,
      the same input is marked/focused, and editing clears the marker.
    * ``_field`` keeps a label and its control together so ``app.tcss`` can
      reflow forms without separating them.
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
        for widget in self.query(".-invalid"):
            widget.remove_class("-invalid")
        widget = self.query_one(f"#{field_id}")
        widget.add_class("-invalid")
        widget.focus()

    def _selected(self, field_id: str, label: str) -> str:
        """The chosen value of a ``Select``; a blank selection is a ``FieldError``.

        ``Select.value`` is ``NoSelection`` until something is chosen, which is
        not a ``str`` and would otherwise blow up later inside an enum lookup.
        """
        value = self.query_one(f"#{field_id}", Select).value
        if not isinstance(value, str):
            raise FieldError(field_id, f"{label}: choose a value")
        return value

    def _read(self, field_id: str, parse: Callable[..., T], label: str, **kwargs: object) -> T:
        """Parse an Input and attach its field id to ValueError."""
        try:
            return parse(self.query_one(f"#{field_id}", Input).value, label, **kwargs)
        except ValueError as exc:
            raise FieldError(field_id, str(exc)) from exc

    def on_input_changed(self, event: Input.Changed) -> None:
        """Editing a previously invalid input clears its error marker."""
        event.input.remove_class("-invalid")

    def refresh_readiness(self) -> None:
        """Hook for pages whose controls depend on connected instruments.

        Pages without connection-dependent controls deliberately do nothing;
        ``IyzeeApp.instruments_changed()`` can therefore refresh every page
        through one base-class contract instead of knowing which pages care.
        """
        return None
