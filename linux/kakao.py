#!/usr/bin/env python3
"""Run KakaoTalk locally on an isolated Ubuntu Wine desktop."""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from runtime import Config, Runtime, RuntimeError, windows_path


COMMANDS = ("doctor", "windows", "rooms", "open", "read", "messages", "search",
            "attachments", "capture", "download", "inspect", "send")
ROOM_COMMANDS = {"open", "read", "messages", "search", "attachments", "capture", "download", "send"}


class AgentError(RuntimeError):
    pass


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def max_chars(value):
    number = int(value)
    if not 1 <= number <= 1_000_000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000000")
    return number


class WineAgent:
    def __init__(self, config, *, timeout=90.0):
        self.config, self.timeout = config, timeout

    def request(self, payload):
        command = [self.config.wine, self.config.windows_python,
                   windows_path(Path(__file__).with_name("wine_agent.py"))]
        try:
            process = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     capture_output=True, env=self.config.environment(), timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise AgentError("Local Wine action timed out. Do not automatically repeat a send or download") from exc
        except OSError as exc:
            raise AgentError(f"Cannot start the local Wine Python agent: {exc}") from exc
        try:
            response = json.loads(process.stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            detail = process.stderr.decode("utf-8", errors="replace")[-1000:]
            raise AgentError("Wine agent returned no valid JSON response: " + detail) from exc
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise AgentError("Wine agent response is invalid")
        if not response["ok"]:
            error = response.get("error", "Local Wine agent failed")
            if isinstance(error, dict):
                error = error.get("message", str(error))
            raise AgentError(str(error))
        if process.returncode:
            raise AgentError(f"Local Wine agent exited with status {process.returncode}; result is uncertain")
        if "result" not in response:
            raise AgentError("Wine agent response has no result")
        return response["result"]

    def command(self, arguments):
        if any(a in {"--interactive", "--stdin"} for a in arguments):
            raise AgentError("Use the isolated Wine desktop; interactive host input is not supported")
        return self.request({"operation": "command", "argv": arguments})

    def ensure_room(self, query):
        if not query.strip():
            raise AgentError("Room title must not be empty")
        rooms = self.command(["rooms"])
        if not isinstance(rooms, list) or any(not isinstance(room, dict) for room in rooms):
            raise AgentError("Wine agent returned an invalid room list")
        matches = [room for room in rooms if room.get("title") == query]
        if len(matches) > 1:
            raise AgentError("Multiple room windows have the same exact title")
        if matches:
            return query
        self.config.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="search-", dir=self.config.state) as folder:
            image = Path(folder) / "results.png"
            probe = self.request({"operation": "search-probe", "query": query, "output": windows_path(image)})
            if not isinstance(probe, dict) or "previous_search" not in probe:
                raise AgentError("Search probe did not supply restoration state")
            try:
                selected = choose_search_row(image, query, int(probe["width"]), int(probe["height"]))
                opened = self.request({"operation": "open-selected", "query": query,
                                       "expected_title": selected["title"], "point": selected["point"]})
                if not isinstance(opened, dict) or opened.get("title") != selected["title"]:
                    raise AgentError("Opened room title did not match the selected search row")
                return opened["title"]
            finally:
                self.request({"operation": "restore-search", "query": probe["previous_search"]})


def choose_search_row(image, query, width, height):
    """Use visible title words only; an ambiguous OCR row never selects a room."""
    if not query.strip() or width <= 120 or height <= 80:
        raise AgentError("Search screenshot dimensions are invalid")
    try:
        result = subprocess.run(["tesseract", str(image), "stdout", "-l", "kor+eng", "--psm", "6", "tsv"],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentError("Local Korean/English Tesseract OCR is unavailable") from exc
    if result.returncode:
        raise AgentError("Tesseract could not read the room search screenshot")
    groups = {}
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            left, top = int(row["left"]), int(row["top"])
            box_width, box_height = int(row["width"]), int(row["height"])
            confidence = float(row["conf"])
        except (KeyError, ValueError) as exc:
            raise AgentError("Tesseract returned malformed word coordinates") from exc
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise AgentError("Tesseract returned invalid word confidence")
        if 55 <= left < width - 65 and 60 <= top < height and box_width > 0 and box_height > 0:
            key = tuple(row.get(k) for k in ("page_num", "block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append((left, top, box_width, box_height, confidence, row["text"].strip()))
    normalized = lambda text: "".join(text.casefold().split())
    candidates = []
    for words in groups.values():
        words.sort()
        title = " ".join(word[5] for word in words)
        if normalized(query) in normalized(title):
            if any(word[4] < 50 for word in words):
                raise AgentError("Room search OCR confidence is too low; inspect the isolated login view")
            left, top = min(w[0] for w in words), min(w[1] for w in words)
            right, bottom = max(w[0] + w[2] for w in words), max(w[1] + w[3] for w in words)
            candidates.append({"title": title, "point": [(left + right) / 2, (top + bottom) / 2]})
    if len(candidates) != 1:
        raise AgentError("Room search must identify exactly one visible title; no room was opened")
    return candidates[0]


def analyze_local(path, *, output_directory=None, limit=60000):
    source = Path(__file__).resolve().parents[1] / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from kakaocli_win.attachment_analysis import analyze_attachment
    return analyze_attachment(path, output_directory=output_directory, max_chars=limit)


def translate_paths(arguments):
    """Paths supplied to the host CLI are host paths, passed through Wine Z:."""
    result, cursor = [], 0
    while cursor < len(arguments):
        value = arguments[cursor]
        if value in {"--output", "--output-dir", "--message-file"}:
            cursor += 1
            if cursor >= len(arguments):
                raise AgentError(f"{value} requires a host path")
            result += [value, windows_path(arguments[cursor])]
        elif any(value.startswith(option + "=") for option in ("--output", "--output-dir", "--message-file")):
            name, path = value.split("=", 1)
            result.append(name + "=" + windows_path(path))
        else:
            result.append(value)
        cursor += 1
    return result


def host_paths(result, field=None):
    if isinstance(result, dict):
        return {key: host_paths(value, key) for key, value in result.items()}
    if isinstance(result, list):
        return [host_paths(value, field) for value in result]
    if field in {"path", "output", "visual_paths", "preview_paths", "output_directory"} and isinstance(result, str) and result[:3].casefold() == "z:\\":
        return "/" + result[3:].replace("\\", "/")
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Local Ubuntu KakaoTalk CLI using Wine and private Xvfb")
    parser.add_argument("--request-timeout", type=positive_float, default=90.0)
    parser.add_argument("--json", action="store_true", help="JSON output (always enabled)")
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime", help="Manage the private local Wine desktop")
    operations = runtime.add_subparsers(dest="runtime_action", required=True)
    for name in ("start", "stop", "status"):
        child = operations.add_parser(name)
        child.add_argument("--json", action="store_true")
    view = operations.add_parser("login-view", help="Authenticated localhost-only optional VNC login view")
    view.add_argument("--password-file", required=True)
    view.add_argument("--port", type=int, default=5903)
    view.add_argument("--json", action="store_true")
    screenshot = operations.add_parser("screenshot", help="Save the private login desktop as a local PNG")
    screenshot.add_argument("--output", required=True)
    screenshot.add_argument("--json", action="store_true")
    for name in COMMANDS:
        remote = commands.add_parser(name, add_help=False)
        remote.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        remote.add_argument("arguments", nargs=argparse.REMAINDER)
    analyze = commands.add_parser("analyze", help="Analyze an Ubuntu local file")
    analyze.add_argument("path")
    analyze.add_argument("--output-dir")
    analyze.add_argument("--max-chars", type=max_chars, default=60000)
    analyze.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args, unparsed = parser.parse_known_args(argv)
    if unparsed and args.command not in COMMANDS:
        parser.error("unrecognized arguments: " + " ".join(unparsed))
    try:
        if args.command == "analyze":
            result = analyze_local(args.path, output_directory=args.output_dir, limit=args.max_chars)
        else:
            config = Config.from_environment()
            runtime = Runtime(config)
            if args.command == "runtime":
                if args.runtime_action == "login-view":
                    result = runtime.login_view(args.password_file, port=args.port)
                elif args.runtime_action == "screenshot":
                    result = runtime.screenshot(args.output)
                else:
                    result = getattr(runtime, args.runtime_action)()
            else:
                # Search, target validation, and the final action share the
                # runtime lifecycle lock: isolated-desktop input cannot interleave.
                with runtime._lock():
                    runtime.require_running()
                    agent = WineAgent(config, timeout=args.request_timeout)
                    arguments = [*unparsed, *args.arguments]
                    if args.command in ROOM_COMMANDS:
                        if not arguments or arguments[0].startswith("--"):
                            raise AgentError("Specify the target room immediately after the command")
                        arguments[0] = agent.ensure_room(arguments[0])
                    elif args.command == "inspect" and "--room" in arguments:
                        index = arguments.index("--room") + 1
                        if index >= len(arguments):
                            raise AgentError("--room requires a room title")
                        arguments[index] = agent.ensure_room(arguments[index])
                    result = host_paths(agent.command([args.command, *translate_paths(arguments)]))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("status") == "error" else 0
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Cancelled"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
