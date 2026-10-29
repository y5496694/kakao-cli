#!/usr/bin/env python3
"""macOS-only KakaoTalk Accessibility CLI with background-only operations."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import re
import subprocess
import sys


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
VERSION = "0.1.0-macos"


class KakaoMacError(RuntimeError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.result = {"status": "error", "error": {"code": code, "message": message}, **details}


def call_adapter(request: dict) -> dict:
    if platform.system() != "Darwin":
        raise KakaoMacError("macos_required", "This adapter runs only on macOS.")
    write_uncertain = bool(request.get("yes") and request.get("operation") in {"send", "download"})
    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(SCRIPT_DIRECTORY / "accessibility.js")],
            input=json.dumps(request, ensure_ascii=False),
            text=True, encoding="utf-8", capture_output=True, timeout=45, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KakaoMacError(
            "adapter_timeout", "Accessibility request timed out; do not automatically retry a write.",
            side_effect_uncertain=write_uncertain,
        ) from exc
    except OSError as exc:
        raise KakaoMacError("adapter_unavailable", str(exc)) from exc
    if completed.returncode:
        raise KakaoMacError("osascript_failed", completed.stderr.strip()[:4000] or "osascript failed.", side_effect_uncertain=write_uncertain)
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise KakaoMacError("invalid_adapter_output", "osascript did not return a JSON object.", side_effect_uncertain=write_uncertain) from exc
    if not isinstance(result, dict):
        raise KakaoMacError("invalid_adapter_output", "osascript did not return a JSON object.", side_effect_uncertain=write_uncertain)
    if result.get("status") == "error":
        error = result.get("error", {})
        details = {key: value for key, value in result.items() if key not in {"status", "error"}}
        raise KakaoMacError(error.get("code", "accessibility_failed"), error.get("message", "Accessibility failed."), **details)
    return result


def accessible_records(nodes: list[dict], *, max_chars: int = 60000) -> tuple[list[dict], bool]:
    """Return AX text without inventing senders, dates, or unseen messages."""
    records = []
    covered_paths: list[list[int]] = []
    remaining = max_chars
    truncated = False
    for node in nodes:
        role = node.get("role", "")
        if node.get("editable") or role not in {"AXStaticText", "AXTextArea", "AXRow", "AXImage"}:
            continue
        node_path = node.get("path", [])
        if any(node_path[:len(parent)] == parent for parent in covered_paths):
            continue
        if role == "AXRow" and node.get("child_count", 0):
            continue
        value = next((node.get(key) for key in ("value", "title", "description") if isinstance(node.get(key), str) and node[key].strip()), "")
        if not value:
            continue
        if remaining <= 0:
            truncated = True
            break
        selected = value[:remaining]
        records.append({"index": len(records), "kind": "image_marker" if role == "AXImage" else "accessible_text", "text": selected, "ax_path": node_path})
        covered_paths.append(node_path)
        remaining -= len(selected)
        truncated |= len(value) > len(selected)
    return records, truncated


def positive_int(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000000:
        raise argparse.ArgumentTypeError("Use an integer between 1 and 1000000.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kakao-macos", description="macOS KakaoTalk AX CLI (background-only; live macOS unverified)")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--json", action="store_true", help="JSON output (also the default)")
    commands = parser.add_subparsers(dest="command", required=True)
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    for name in ("doctor", "windows", "rooms"):
        commands.add_parser(name, parents=[output])
    for name in ("messages", "read", "attachments"):
        command = commands.add_parser(name, parents=[output])
        command.add_argument("room", help="Exact title of an already open chat window")
        command.add_argument("--limit", type=positive_int, default=50)
        command.add_argument("--max-chars", type=positive_int, default=60000)
        command.add_argument("--contains")
    send = commands.add_parser("send", parents=[output], help="Preview by default; --yes explicitly submits")
    send.add_argument("room", help="Exact title of an already open chat window")
    source = send.add_mutually_exclusive_group(required=True)
    source.add_argument("message", nargs="?")
    source.add_argument("--message-file")
    source.add_argument("--stdin", action="store_true")
    send.add_argument("--yes", action="store_true")
    send.add_argument("--dry-run", action="store_true")
    download = commands.add_parser("download", parents=[output], help="Complete an already open app-owned Save sheet; preview by default")
    download.add_argument("room", nargs="?", help="Require the pending sheet to belong to this exact chat window")
    destination = download.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", help="Absolute target path; requires exposed full-path or directory controls")
    destination.add_argument("--filename", help="Save to the folder already selected in the pending Save sheet")
    download.add_argument("--yes", action="store_true")
    download.add_argument("--dry-run", action="store_true")
    analyze = commands.add_parser("analyze", parents=[output])
    analyze.add_argument("path")
    analyze.add_argument("--output-dir")
    analyze.add_argument("--max-chars", type=positive_int, default=60000)
    return parser


def _message(args) -> str:
    if args.message_file:
        value = Path(args.message_file).expanduser().read_text(encoding="utf-8-sig")
    elif args.stdin:
        value = sys.stdin.read()
    else:
        value = args.message
    if not value or not value.strip() or "\0" in value:
        raise KakaoMacError("invalid_message", "Message must be nonempty and contain no NUL characters.")
    if len(value) > 60000:
        raise KakaoMacError("message_too_long", "Message is limited to 60000 characters.")
    return value


def execute(args) -> dict:
    if args.command == "analyze":
        sys.path.insert(0, str(SCRIPT_DIRECTORY.parent / "src"))
        from kakaocli_win.attachment_analysis import analyze_attachment
        return analyze_attachment(args.path, output_directory=args.output_dir, max_chars=args.max_chars)
    if args.command in {"doctor", "windows", "rooms"}:
        return call_adapter({"operation": args.command})
    if args.command == "send":
        return call_adapter({"operation": "send", "room": args.room, "message": _message(args), "yes": args.yes and not args.dry_run})
    if args.command == "download":
        if args.filename and (args.filename in {".", ".."} or any(char in args.filename for char in ("/", "\\", "\0", ":"))):
            raise KakaoMacError("invalid_filename", "--filename must be a single filename without path separators.")
        target = None
        if args.output:
            output = Path(args.output).expanduser()
            if not output.is_absolute():
                raise KakaoMacError("absolute_path_required", "--output must be an absolute path.")
            target = str(output)
            if output.exists():
                raise KakaoMacError("destination_exists", "Destination already exists; replacement is not automated.")
            if not output.parent.is_dir():
                raise KakaoMacError("directory_missing", "Create the output directory before saving.")
        result = call_adapter({"operation": "download", "room": args.room, "output": target, "filename": args.filename, "yes": args.yes and not args.dry_run})
        if target and result.get("save_requested"):
            # An AXPress return value cannot prove download completion.
            result["path"] = target
            result["file_present"] = Path(target).is_file()
            result["download_confirmed"] = False
        return result
    snapshot = call_adapter({"operation": "snapshot", "room": args.room})
    records, text_truncated = accessible_records(snapshot.get("nodes", []), max_chars=args.max_chars)
    if args.command == "attachments":
        markers = re.compile(r"사진|이미지|동영상|첨부|파일|\b(?:photo|image|video|file|attachment)\b", re.IGNORECASE)
        records = [record for record in records if record["kind"] == "image_marker" or markers.search(record["text"])]
    if args.contains:
        records = [record for record in records if args.contains.casefold() in record["text"].casefold()]
    return {
        "status": "ok", "room": args.room, "coverage": "accessible_ui_only",
        "complete_history": False, "background_only": True,
        "truncated": bool(snapshot.get("truncated") or text_truncated or len(records) > args.limit),
        "records": records[-args.limit:], "matched_records": len(records),
        "note": "AX exposes text/attachment markers only. No sender, date, full history, or downloadable URL is inferred.",
    }


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if platform.system() != "Darwin":
            raise KakaoMacError("macos_required", "This folder contains the macOS-only version; use the root CLI on Windows.")
        result = execute(args)
    except KakaoMacError as exc:
        result = exc.result
    except (OSError, ValueError) as exc:
        result = {"status": "error", "error": {"code": "local_error", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("status") in {"error", "unsupported"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
