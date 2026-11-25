"""Keyboard navigation, visible focus rings, and deterministic Tab order."""
from __future__ import annotations

import tkinter as tk

FOCUS_BORDER_COLOR = "#60A5FA"
FOCUS_BORDER_WIDTH = 2
_UNSET = object()


def focus_target(widget):
    """Return the underlying Tk widget that receives keyboard focus."""
    if widget is None:
        return None
    for attr in ("_entry", "_canvas", "_textbox"):
        inner = getattr(widget, attr, None)
        if inner is not None:
            return inner
    return widget


def _widget_exists(widget):
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except (RuntimeError, tk.TclError):
        return False


def _widget_state(widget):
    try:
        return str(widget.cget("state")).lower()
    except Exception:
        return "normal"


def is_control_traversable(widget):
    """Return True if widget is mapped, existing, and enabled for Tab focus."""
    if not _widget_exists(widget):
        return False
    if _widget_state(widget) == "disabled":
        return False
    try:
        if not widget.winfo_ismapped():
            return False
    except (RuntimeError, tk.TclError):
        return False
    target = focus_target(widget)
    if target is None or not _widget_exists(target):
        return False
    return _widget_state(target) != "disabled"


def _save_border(widget):
    if hasattr(widget, "_conduit_saved_border"):
        return widget._conduit_saved_border
    try:
        width = widget.cget("border_width")
    except Exception:
        width = _UNSET
    try:
        color = widget.cget("border_color")
    except Exception:
        color = _UNSET
    saved = (width, color)
    widget._conduit_saved_border = saved
    return saved


def _apply_ctk_focus_border(widget, focused):
    saved_width, saved_color = _save_border(widget)
    kwargs = {}
    if focused:
        if saved_width is not _UNSET:
            kwargs["border_width"] = max(FOCUS_BORDER_WIDTH, int(saved_width or 0))
        if saved_color is not _UNSET:
            kwargs["border_color"] = FOCUS_BORDER_COLOR
    else:
        if saved_width is not _UNSET:
            kwargs["border_width"] = saved_width
        if saved_color is not _UNSET:
            kwargs["border_color"] = saved_color
    if kwargs:
        try:
            widget.configure(**kwargs)
        except Exception:
            pass


def _apply_canvas_focus_ring(canvas, focused):
    if canvas is None or not _widget_exists(canvas):
        return
    if not hasattr(canvas, "_conduit_saved_highlight"):
        try:
            canvas._conduit_saved_highlight = (
                canvas.cget("highlightthickness"),
                canvas.cget("highlightcolor"),
                canvas.cget("highlightbackground"),
            )
        except Exception:
            canvas._conduit_saved_highlight = None
    saved = canvas._conduit_saved_highlight
    if saved is None:
        return
    try:
        if focused:
            canvas.configure(
                highlightthickness=FOCUS_BORDER_WIDTH,
                highlightcolor=FOCUS_BORDER_COLOR,
                highlightbackground=FOCUS_BORDER_COLOR,
            )
        else:
            canvas.configure(
                highlightthickness=saved[0],
                highlightcolor=saved[1],
                highlightbackground=saved[2],
            )
    except Exception:
        pass


def enable_focus_ring(widget):
    """Attach a clear visible focus indicator to a CTk or Tk control."""
    target = focus_target(widget)
    if target is None:
        return None
    if getattr(widget, "_conduit_focus_ring_bound", False):
        return target
    widget._conduit_focus_ring_bound = True

    has_ctk_border = False
    try:
        widget.cget("border_color")
        widget.cget("border_width")
        has_ctk_border = True
    except Exception:
        has_ctk_border = False

    if has_ctk_border:
        _save_border(widget)

    def _on_focus_in(_event=None):
        if not _widget_exists(widget):
            return
        if has_ctk_border:
            _apply_ctk_focus_border(widget, True)
        else:
            _apply_canvas_focus_ring(target, True)

    def _on_focus_out(_event=None):
        if not _widget_exists(widget):
            return
        if has_ctk_border:
            _apply_ctk_focus_border(widget, False)
        else:
            _apply_canvas_focus_ring(target, False)

    try:
        target.bind("<FocusIn>", _on_focus_in, add="+")
        target.bind("<FocusOut>", _on_focus_out, add="+")
    except Exception:
        pass
    return target


def make_button_keyboard_accessible(button):
    """Enable Tab focus, focus ring, and Enter/Space activation on a button."""
    target = focus_target(button)
    if target is None:
        return None
    try:
        target.configure(takefocus=True)
    except Exception:
        pass
    enable_focus_ring(button)
    if getattr(button, "_conduit_button_keys_bound", False):
        return target
    button._conduit_button_keys_bound = True

    def _activate(_event=None):
        if not _widget_exists(button) or _widget_state(button) == "disabled":
            return "break"
        button.invoke()
        return "break"

    for sequence in ("<Return>", "<KP_Enter>", "<space>"):
        target.bind(sequence, _activate, add="+")
    return target


def make_switch_keyboard_accessible(switch):
    """Enable Tab focus, focus ring, and Enter/Space toggle on a CTkSwitch."""
    target = focus_target(switch)
    if target is None:
        return None
    try:
        target.configure(takefocus=True)
    except Exception:
        pass
    enable_focus_ring(switch)
    if getattr(switch, "_conduit_switch_keys_bound", False):
        return target
    switch._conduit_switch_keys_bound = True

    def _toggle(_event=None):
        if not _widget_exists(switch) or _widget_state(switch) == "disabled":
            return "break"
        switch.toggle()
        return "break"

    for sequence in ("<Return>", "<KP_Enter>", "<space>"):
        target.bind(sequence, _toggle, add="+")
    return target


def make_entry_keyboard_accessible(entry):
    """Enable focus ring on a CTkEntry."""
    target = focus_target(entry)
    if target is None:
        return None
    try:
        target.configure(takefocus=True)
    except Exception:
        pass
    enable_focus_ring(entry)
    return target


def make_combobox_keyboard_accessible(combobox):
    """Enable focus ring and Up/Down value cycling on a CTkComboBox."""
    target = make_entry_keyboard_accessible(combobox)
    if target is None or getattr(combobox, "_conduit_combo_keys_bound", False):
        return target
    combobox._conduit_combo_keys_bound = True

    def _step(delta):
        if not _widget_exists(combobox) or _widget_state(combobox) == "disabled":
            return "break"
        try:
            values = list(combobox.cget("values") or ())
        except Exception:
            values = []
        if not values:
            return "break"
        current = combobox.get()
        if current in values:
            next_index = (values.index(current) + delta) % len(values)
        else:
            next_index = 0 if delta > 0 else len(values) - 1
        choice = values[next_index]
        combobox.set(choice)
        command = getattr(combobox, "_command", None)
        if callable(command):
            command(choice)
        return "break"

    target.bind("<Down>", lambda _e: _step(1), add="+")
    target.bind("<Up>", lambda _e: _step(-1), add="+")
    return target


def disable_textbox_tab_trap(textbox):
    """Ensure read-only CTkTextbox widgets do not trap Tab focus."""
    target = focus_target(textbox)
    if target is not None:
        try:
            target.configure(takefocus=False)
        except Exception:
            pass
    try:
        textbox.configure(takefocus=False)
    except Exception:
        pass


def tabview_tab_buttons(tabview):
    """Return CTkTabview's segmented tab buttons in display order."""
    seg = getattr(tabview, "_segmented_button", None)
    if seg is None:
        return []
    buttons_dict = getattr(seg, "_buttons_dict", {})
    names = list(getattr(seg, "_value_list", ()) or buttons_dict.keys())
    return [buttons_dict[name] for name in names if name in buttons_dict]


def make_tabview_keyboard_accessible(tabview, on_tab_selected=None):
    """Make CTkTabview tabs focusable and switchable with Left/Right/Enter/Space."""
    seg = getattr(tabview, "_segmented_button", None)
    if seg is None:
        return []
    buttons_dict = getattr(seg, "_buttons_dict", {})
    names = list(getattr(seg, "_value_list", ()) or buttons_dict.keys())
    ordered = [(name, buttons_dict[name]) for name in names if name in buttons_dict]

    for index, (name, button) in enumerate(ordered):
        target = focus_target(button)
        if target is None:
            continue
        try:
            target.configure(takefocus=True)
        except Exception:
            pass
        enable_focus_ring(button)
        if getattr(button, "_conduit_tab_keys_bound", False):
            continue
        button._conduit_tab_keys_bound = True

        def _select(tab_name=name, tab_btn=button):
            if not _widget_exists(tabview):
                return "break"
            tabview.set(tab_name)
            btn_target = focus_target(tab_btn)
            if btn_target is not None and _widget_exists(btn_target):
                btn_target.focus_set()
            if callable(on_tab_selected):
                on_tab_selected(tab_name)
            return "break"

        def _move(delta, idx=index):
            if not ordered:
                return "break"
            next_name, next_btn = ordered[(idx + delta) % len(ordered)]
            tabview.set(next_name)
            next_target = focus_target(next_btn)
            if next_target is not None and _widget_exists(next_target):
                next_target.focus_set()
            if callable(on_tab_selected):
                on_tab_selected(next_name)
            return "break"

        for sequence in ("<Return>", "<KP_Enter>", "<space>"):
            target.bind(sequence, lambda _e, fn=_select: fn(), add="+")
        target.bind("<Right>", lambda _e, fn=_move: fn(1), add="+")
        target.bind("<Left>", lambda _e, fn=_move: fn(-1), add="+")

    return [btn for _, btn in ordered]


def _matching_control_index(current_focus, ordered):
    if current_focus is None:
        return None
    for index, control in enumerate(ordered):
        target = focus_target(control)
        if current_focus is control or current_focus is target:
            return index
        # Walk up parents in case a nested child widget currently holds focus.
        node = current_focus
        while node is not None:
            if node is control or node is target:
                return index
            node = getattr(node, "master", None)
    return None


def install_tab_traversal(window, get_controls):
    """Install deterministic Tab / Shift+Tab traversal across visible controls."""

    def _step(delta):
        ordered = [control for control in get_controls() if is_control_traversable(control)]
        if not ordered:
            return "break"
        try:
            current = window.focus_get()
        except Exception:
            current = None
        index = _matching_control_index(current, ordered)
        if index is None:
            next_control = ordered[0] if delta > 0 else ordered[-1]
        else:
            next_control = ordered[(index + delta) % len(ordered)]
        target = focus_target(next_control)
        if target is not None and _widget_exists(target):
            target.focus_set()
        return "break"

    window.bind("<Tab>", lambda _e: _step(1))
    window.bind("<Shift-Tab>", lambda _e: _step(-1))
    window.bind("<ISO_Left_Tab>", lambda _e: _step(-1))


def focus_first_available(controls):
    """Focus the first traversable control in controls, returning True on success."""
    for control in controls:
        if is_control_traversable(control):
            target = focus_target(control)
            if target is not None and _widget_exists(target):
                target.focus_set()
                return True
    return False
