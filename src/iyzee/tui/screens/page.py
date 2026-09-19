"""Shared base class for the app's pages (Connect, Sweep, Traces)."""

from __future__ import annotations

from textual.containers import VerticalScroll


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
    """
