"""Windows-Python agent for KakaoTalk inside an explicitly isolated Wine desktop.

The JSON protocol never invokes a shell or Frida. Real focus is permitted only
after both Wine detection and the isolated-desktop opt-in have succeeded.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import json
import math
import os
from pathlib import Path
import sys
import time


REQUEST_LIMIT = 1024 * 1024
ALLOWED_COMMANDS = {
    "doctor", "windows", "rooms", "open", "read", "messages", "search", "send",
    "inspect", "attachments", "capture", "download",
}


class AgentError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require_isolated_wine():
    if os.environ.get("KAKAOCLI_ISOLATED_DESKTOP") != "1":
        raise AgentError("isolation_required", "KAKAOCLI_ISOLATED_DESKTOP=1 is required; no app was controlled.")
    try:
        library = ctypes.WinDLL("ntdll")
        version = library.wine_get_version
        version.argtypes = []
        version.restype = ctypes.c_char_p
        value = version()
        if not value:
            raise ValueError("Empty Wine version")
        return value.decode("ascii", errors="strict")
    except (AttributeError, OSError, UnicodeError, ValueError) as exc:
        raise AgentError("wine_required", "This agent refuses native Windows; Wine ntdll.wine_get_version is required.") from exc


def _load_modules():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from kakaocli_win import attachments, backend, cli
    from kakaocli_win.transcript import loaded_messages
    return backend, attachments, cli, loaded_messages


def _string(value, label, *, allow_empty=False):
    if not isinstance(value, str) or "\0" in value or len(value) > 60000 or (not allow_empty and not value.strip()):
        raise AgentError("invalid_request", f"{label} must be a valid {'possibly empty ' if allow_empty else ''}string.")
    return value


def _one(items, label):
    if len(items) != 1:
        raise AgentError("ambiguous_control", f"Expected one {label}; found {len(items)}.")
    return items[0]


def _find_room(b, title):
    _string(title, "room")
    matches = [window for window in b.list_windows() if window.title == title and window.kind == "chat"]
    if len(matches) > 1:
        raise AgentError("ambiguous_room", "Multiple windows have the same exact chat title.")
    return matches[0] if matches else None


def _existing_room(b, title):
    window = _find_room(b, title)
    if window is None:
        raise AgentError("room_not_open", "The host must resolve and open the exact room before this operation.")
    b._assert_room_title(window)
    return window


def _modal_windows(b, a, window):
    return [hwnd for hwnd in a._windows(window.pid)
            if b.win32gui.GetClassName(hwnd) == "#32770" and b.win32gui.IsWindowVisible(hwnd)]


def _no_modals(b, a, window):
    if _modal_windows(b, a, window):
        raise AgentError("pending_modal", "Complete the existing app-owned dialog before touching another chat control.")
    b._assert_window_enabled(window.hwnd)


def _main_controls(b, a):
    main = b.find_main_window()
    if main is None:
        raise AgentError("main_not_found", "Launch KakaoTalk and log in inside the isolated Wine desktop first.")
    _no_modals(b, a, main)
    view = _one([h for h in b.enum_children(main.hwnd)
                 if b.win32gui.GetWindowText(h).startswith("ChatRoomListView_")], "chat-list view")
    edit = _one([h for h in b.enum_children(view)
                 if b.win32gui.GetClassName(h) == "Edit" and b.win32gui.GetDlgCtrlID(h) == 100], "chat search field")
    b._assert_process_target(edit, root=main.hwnd, pid=main.pid)
    return main, view, edit


def _set_search(b, edit, query):
    b.win32gui.SendMessage(edit, b.win32con.WM_SETTEXT, 0, query)
    if b._read_input_text(edit) != query:
        raise AgentError("search_not_set", "Search text readback did not match; no result was selected.")


def _search_probe(request, b, a):
    query = _string(request.get("query"), "query")
    destination = Path(_string(request.get("output"), "output"))
    if not destination.is_absolute() or destination.exists():
        raise AgentError("invalid_output", "Search screenshot needs an absolute, unused output path.")
    main, _, edit = _main_controls(b, a)
    previous = b._read_input_text(edit)
    try:
        b._focus(main.hwnd)
        _set_search(b, edit, query)
        time.sleep(0.35)
        rectangle = a._capture(main.hwnd, destination)
    except Exception:
        _set_search(b, edit, previous)
        raise
    return {"main": hex(main.hwnd), "previous_search": previous, "query": query,
            "path": str(destination), "width": rectangle[2] - rectangle[0], "height": rectangle[3] - rectangle[1]}


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AgentError("invalid_point", "point must contain X and Y source-image pixels.")
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) for number in value):
        raise AgentError("invalid_point", "point coordinates must be finite numbers.")
    return tuple(round(number) for number in value)


def _click(b, root, control, point, *, double=False):
    x, y = _point(point)
    left, top, right, bottom = b.win32gui.GetWindowRect(root)
    if not 0 <= x < right - left or not 0 <= y < bottom - top:
        raise AgentError("point_outside_window", "Point lies outside the captured window.")
    x, y = b.win32gui.ScreenToClient(control, (left + x, top + y))
    c_left, c_top, c_right, c_bottom = b.win32gui.GetClientRect(control)
    if not c_left <= x < c_right or not c_top <= y < c_bottom:
        raise AgentError("point_outside_control", "Point lies outside the selected list control.")
    b._focus(control)
    packed = (y & 0xFFFF) << 16 | (x & 0xFFFF)
    messages = [(0x0201, 1), (0x0202, 0)]
    if double:
        messages += [(0x0203, 1), (0x0202, 0)]
    for message, buttons in messages:
        b.win32gui.PostMessage(control, message, buttons, packed)


def _open_selected(request, b, a):
    query = _string(request.get("query"), "query")
    expected = _string(request.get("expected_title"), "expected_title")
    point = _point(request.get("point"))
    main, view, edit = _main_controls(b, a)
    if b._read_input_text(edit) != query:
        raise AgentError("search_changed", "Search changed after OCR verification; no row was opened.")
    results = _one([h for h in b.enum_children(view)
                    if b.win32gui.GetWindowText(h).startswith("SearchListCtrl_")], "search result list")
    b._assert_process_target(results, root=main.hwnd, pid=main.pid)
    _click(b, main.hwnd, results, point, double=True)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        opened = _find_room(b, expected)
        if opened:
            b._assert_room_title(opened)
            return {"hwnd": hex(opened.hwnd), "title": opened.title}
        time.sleep(0.1)
    raise AgentError("opened_title_mismatch", "No chat with the OCR-verified exact title opened; no message was sent.")


def _save_pending(window, dialog, args, b, a):
    if not a._owned_by_room(dialog, window):
        raise AgentError("dialog_owner_mismatch", "Save dialog does not belong to the requested room.")
    edit = _one([h for h in b.enum_children(dialog) if b.win32gui.GetClassName(h) == "Edit"
                 and b.win32gui.GetDlgCtrlID(h) in {1001, 1148}], "save filename field")
    original = b._read_input_text(edit)
    path = a._destination(original, args.kind, args.output_dir, args.filename)
    button = b.win32gui.GetDlgItem(dialog, 1)
    if not button or not any(word in b.win32gui.GetWindowText(button).casefold() for word in ("save", "저장")):
        raise AgentError("save_button_missing", "The named Save button was not identified.")
    b._focus(edit)
    b.win32gui.SendMessage(edit, b.win32con.WM_SETTEXT, 0, str(path))
    if b._read_input_text(edit) != str(path):
        raise AgentError("save_path_mismatch", "Save path readback failed; no Save action was requested.")
    b.win32gui.PostMessage(button, b.win32con.BM_CLICK, 0, 0)
    deadline = time.monotonic() + args.timeout
    fallback_after = time.monotonic() + 0.6
    fallback_used, previous, stable = False, -1, 0
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.is_file() else 0
        stable = stable + 1 if size > 0 and size == previous else 0
        previous = size
        open_dialog = b.win32gui.IsWindow(dialog)
        if stable >= 3 and not open_dialog:
            return {"saved": True, "room": window.title, "path": str(path), "filename": path.name,
                    "bytes": size, "sha256": a._sha256(path), "kind": args.kind,
                    "save_dialog_closed": True, "isolated_desktop": True, "runtime": "wine"}
        if open_dialog and not size and not fallback_used and time.monotonic() >= fallback_after:
            # Real focus is confined to the Wine/Xvfb desktop checked at entry.
            if not a._owned_by_room(dialog, window) or b._read_input_text(edit) != str(path):
                raise AgentError("save_dialog_changed", "Pending Save dialog changed; no fallback input was sent.")
            if not any(word in b.win32gui.GetWindowText(button).casefold() for word in ("save", "저장")):
                raise AgentError("save_button_changed", "The pending dialog's Save button changed; no fallback input was sent.")
            b._focus(button)
            b._assert_target(button, root=dialog, pid=window.pid)
            b._press(b.win32con.VK_SPACE)
            fallback_used = True
        time.sleep(0.1)
    raise AgentError("download_unconfirmed", "Original-file save was not confirmed; inspect the pending Wine dialog before retrying.")


def _download(args, window, b, a):
    pending = a._save_dialogs(window)
    all_modals = _modal_windows(b, a, window)
    if any(hwnd not in pending for hwnd in all_modals):
        raise AgentError("pending_modal", "An unrelated app-owned modal dialog must be resolved first.")
    if pending:
        if args.at is not None:
            raise AgentError("pending_modal", "Finish the existing Save dialog with --dialog before selecting another attachment.")
        return _save_pending(window, _one(pending, "owned Save dialog"), args, b, a)
    if args.mode == "dialog":
        raise AgentError("save_dialog_missing", "There is no pending Save dialog owned by this room.")
    _no_modals(b, a, window)
    if args.at is not None:
        transcript = b._transcript_handle(window.hwnd)
        _click(b, window.hwnd, transcript, args.at, double=args.kind == "image")
        time.sleep(0.35)
    elif args.kind == "file":
        raise AgentError("file_save_point_required", "File downloads require --at on the file Save button or an existing --dialog.")
    deadline = time.monotonic() + args.timeout
    viewer_saved = False
    while time.monotonic() < deadline:
        pending = a._save_dialogs(window)
        if pending:
            return _save_pending(window, _one(pending, "owned Save dialog"), args, b, a)
        if args.kind == "image" and not viewer_saved:
            viewers = [h for h in a._windows(window.pid) if a._viewer(h, window)]
            if len(viewers) > 1:
                raise AgentError("ambiguous_viewer", "Multiple photo viewers match this room.")
            if viewers:
                b._focus(viewers[0])
                b._press(ord("S"), (b.win32con.VK_CONTROL,))
                viewer_saved = True
        time.sleep(0.1)
    raise AgentError("save_dialog_missing", "Attachment action did not expose a unique owned Save dialog.")


def _read_message(args):
    if args.stdin:
        raise AgentError("stdin_reserved", "stdin carries the JSON request; send message text in argv or --message-file.")
    if args.message_file:
        source = Path(args.message_file)
        if source.stat().st_size > REQUEST_LIMIT:
            raise AgentError("message_too_large", "Message file exceeds 1 MiB.")
        message = source.read_text(encoding="utf-8-sig")
    else:
        message = args.message
    return _string(message, "message")


def _command(request, b, a, cli, loaded_messages):
    arguments = request.get("argv")
    if not isinstance(arguments, list) or not arguments or len(arguments) > 256 or any(not isinstance(value, str) or "\0" in value for value in arguments):
        raise AgentError("invalid_argv", "argv must be a nonempty list of at most 256 strings.")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            args = cli.build_parser().parse_args(arguments)
        except SystemExit as exc:
            raise AgentError("invalid_command", "Invalid Wine command arguments; use the host CLI help.") from exc
    if args.command not in ALLOWED_COMMANDS:
        raise AgentError("unsupported_command", "This Wine agent does not expose that root CLI command.")
    if args.command == "doctor":
        return {**b.doctor(), "runtime": "wine", "isolated_desktop": True}
    if args.command in {"windows", "rooms"}:
        return [window.to_dict() for window in (b.list_windows() if args.command == "windows" else b.list_rooms())]
    if args.command == "inspect":
        if args.room:
            _existing_room(b, args.room)
        return b.inspect_tree(args.room)
    window = _existing_room(b, args.room)
    if args.command == "download":
        return _download(args, window, b, a)
    if args.command == "capture":
        pending = a._save_dialogs(window)
        target = _one(pending, "owned Save dialog") if pending else window.hwnd
        destination = Path(args.output)
        if not destination.is_absolute() or destination.exists():
            raise AgentError("invalid_output", "Capture needs an absolute, unused output path.")
        if getattr(args, "scroll", None):
            _no_modals(b, a, window)
            target = b._transcript_handle(window.hwnd)
            b._focus(target)
            request_code = {"up": 2, "down": 3, "top": 6, "bottom": 7}[args.scroll]
            b.win32gui.PostMessage(target, 0x0115, request_code, 0)
            time.sleep(0.15)
            target = window.hwnd
        rectangle = a._capture(target, destination)
        return {"room": window.title, "path": str(destination), "rect": rectangle,
                "isolated_desktop": True, "runtime": "wine", "save_dialog_open": bool(pending)}
    _no_modals(b, a, window)
    if args.command == "open":
        return {"opened": b.open_room(window.title, exact=True, interactive=True).to_dict()}
    if args.command == "send":
        message = _read_message(args)
        if args.dry_run:
            return {"status": "preview", "submitted": False, "message": message, **b.preview_send(window.title, exact=True)}
        if not args.yes:
            raise AgentError("confirmation_required", "Explicit --yes is required for Wine-agent message submission.")
        submitted = b.send_message(window.title, message, exact=True, interactive=True)
        return {"status": "submitted_unconfirmed", "room": submitted.title, "submitted": True,
                "delivery_confirmed": False, "runtime": "wine", "isolated_desktop": True}
    if args.command == "attachments":
        return a.list_attachments(window.title, limit=args.limit, interactive=True)
    _, text = b.read_visible(window.title, exact=True, interactive=True)
    if args.command == "read":
        return {"room": window.title, "text": text, "coverage": "loaded_ui_only", "complete_history": False}
    contains = args.query if args.command == "search" else args.contains
    if contains is not None and not contains.strip():
        raise AgentError("empty_query", "Message search query cannot be empty.")
    return {"room": window.title, **loaded_messages(text, contains=contains, limit=args.limit)}


def handle_request(request):
    wine_version = _require_isolated_wine()
    if not isinstance(request, dict):
        raise AgentError("invalid_request", "One JSON object is required.")
    operation = request.get("operation")
    if operation not in {"command", "search-probe", "open-selected", "restore-search"}:
        raise AgentError("unknown_operation", "Unsupported Wine agent operation.")
    b, a, cli, loaded_messages = _load_modules()
    # Root backend fallback opens missing rooms through Frida. The host performs
    # OCR resolution instead, so even a race must remain existing-room-only.
    original_resolver = b._resolve_room
    b._resolve_room = lambda room, exact=False: _existing_room(b, room)
    try:
        if operation == "command":
            result = _command(request, b, a, cli, loaded_messages)
            if isinstance(result, dict):
                result.setdefault("wine_version", wine_version)
            return result
        if operation == "search-probe":
            return _search_probe(request, b, a)
        if operation == "open-selected":
            return _open_selected(request, b, a)
        query = _string(request.get("query"), "query", allow_empty=True)
        _, _, edit = _main_controls(b, a)
        _set_search(b, edit, query)
        return {"restored": True, "search": query}
    finally:
        b._resolve_room = original_resolver


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        source = getattr(sys.stdin, "buffer", sys.stdin)
        raw = source.read(REQUEST_LIMIT + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > REQUEST_LIMIT:
            raise AgentError("request_too_large", "JSON request exceeds 1 MiB.")
        request = json.loads(raw.decode("utf-8-sig"))
        result = handle_request(request)
        response, code = {"ok": True, "result": result}, 0
    except Exception as exc:
        response, code = {"ok": False, "error": {"code": getattr(exc, "code", "operation_failed"), "message": str(exc)}}, 2
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
