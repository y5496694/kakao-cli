"""Check the injected JavaScript ABI against independently declared Win32 structs.

Fixed-width pointer fields let both target layouts be tested on either host ABI.
These are synthetic buffers; the tests never attach to or operate KakaoTalk.
"""
import ctypes
import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest


HELPER = Path(__file__).parents[1] / "src" / "kakaocli_win" / "win32_abi.js"


def native_structures(pointer_size):
    pointer = ctypes.c_uint32 if pointer_size == 4 else ctypes.c_uint64
    dword, word = ctypes.c_uint32, ctypes.c_uint16

    class GetTextEx(ctypes.Structure):
        _fields_ = [("cb", dword), ("flags", dword), ("codepage", dword),
                    ("lpDefaultChar", pointer), ("lpUsedDefChar", pointer)]

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", dword), ("hwndOwner", pointer),
            ("hInstance", pointer), ("lpstrFilter", pointer),
            ("lpstrCustomFilter", pointer), ("nMaxCustFilter", dword),
            ("nFilterIndex", dword), ("lpstrFile", pointer),
            ("nMaxFile", dword), ("lpstrFileTitle", pointer),
            ("nMaxFileTitle", dword), ("lpstrInitialDir", pointer),
            ("lpstrTitle", pointer), ("Flags", dword),
            ("nFileOffset", word), ("nFileExtension", word),
            ("lpstrDefExt", pointer), ("lCustData", pointer),
            ("lpfnHook", pointer), ("lpTemplateName", pointer),
            ("pvReserved", pointer), ("dwReserved", dword), ("FlagsEx", dword),
        ]

    return GetTextEx, OpenFileName


@pytest.fixture(scope="module")
def evaluate():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the injected ABI helper")

    def run(expression):
        program = """
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const value = vm.runInNewContext(source + '\\n' + process.argv[2], {Buffer});
process.stdout.write(JSON.stringify(value));
"""
        completed = subprocess.run(
            [node, "-e", program, str(HELPER), expression],
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        return json.loads(completed.stdout)

    return run


@pytest.mark.parametrize("pointer_size,abi", [(4, "stdcall"), (8, "win64")])
def test_layout_matches_win32_declarations(evaluate, pointer_size, abi):
    layout = evaluate(f"win32Layout({pointer_size})")
    text, dialog = native_structures(pointer_size)
    assert layout["abi"] == abi
    assert layout["getTextEx"] == {
        "size": ctypes.sizeof(text),
        "defaultChar": text.lpDefaultChar.offset,
        "usedDefaultChar": text.lpUsedDefChar.offset,
    }
    fields = {
        "owner": "hwndOwner", "file": "lpstrFile", "maxFile": "nMaxFile",
        "fileTitle": "lpstrFileTitle", "maxFileTitle": "nMaxFileTitle",
        "fileOffset": "nFileOffset", "fileExtension": "nFileExtension",
        "defaultExtension": "lpstrDefExt",
    }
    actual = layout["openFileName"]
    assert {key: actual[key] for key in fields} == {
        key: getattr(dialog, field).offset for key, field in fields.items()
    }
    # Any accepted structure must contain every field the hook accesses.
    assert dialog.lpstrDefExt.offset + pointer_size <= actual["minSize"] <= ctypes.sizeof(dialog)


@pytest.mark.parametrize("pointer_size", [4, 8])
def test_dialog_writes_preserve_neighboring_flags_and_guards(evaluate, pointer_size):
    """Exercise offsets on a byte buffer, including two adjacent 16-bit fields."""
    encoded = evaluate("""(() => {
  const pointerSize = %d;
  const offsets = win32Layout(pointerSize).openFileName;
  const buffer = Buffer.alloc(192, 0xa5), base = 8;
  const pointer = (field, value) => pointerSize === 4
    ? buffer.writeUInt32LE(value, base + offsets[field])
    : buffer.writeBigUInt64LE(BigInt(value), base + offsets[field]);
  pointer('owner', 0x10203040);
  pointer('file', 0x11223344);
  buffer.writeUInt32LE(4096, base + offsets.maxFile);
  pointer('fileTitle', 0x55667788);
  buffer.writeUInt32LE(260, base + offsets.maxFileTitle);
  buffer.writeUInt16LE(19, base + offsets.fileOffset);
  buffer.writeUInt16LE(27, base + offsets.fileExtension);
  pointer('defaultExtension', 0x12345678);
  return buffer.toString('hex');
})()""" % pointer_size)
    actual = bytes.fromhex(encoded)
    _, dialog = native_structures(pointer_size)
    expected = bytearray([0xA5] * 192)
    pointer = "<I" if pointer_size == 4 else "<Q"
    for field, value in [("hwndOwner", 0x10203040), ("lpstrFile", 0x11223344),
                         ("lpstrFileTitle", 0x55667788), ("lpstrDefExt", 0x12345678)]:
        struct.pack_into(pointer, expected, 8 + getattr(dialog, field).offset, value)
    for field, value in [("nMaxFile", 4096), ("nMaxFileTitle", 260)]:
        struct.pack_into("<I", expected, 8 + getattr(dialog, field).offset, value)
    for field, value in [("nFileOffset", 19), ("nFileExtension", 27)]:
        struct.pack_into("<H", expected, 8 + getattr(dialog, field).offset, value)
    assert actual == bytes(expected)


@pytest.mark.parametrize("pointer_size", [0, 2, 16, "'4'", "null"])
def test_unknown_pointer_layout_is_rejected(evaluate, pointer_size):
    assert evaluate("""(() => {
  try { win32Layout(%s); return false; } catch (_) { return true; }
})()""" % pointer_size)


@pytest.mark.parametrize("point,source,target,expected", [
    ([320, 180], [1280, 720], [640, 360], [160, 90]),
    # A 150% capture maps back to native logical coordinates.
    ([150, 75], [750, 1500], [500, 1000], [100, 50]),
    # Each axis uses its own scale; distorted captures cannot reuse x's ratio.
    ([90, 180], [1200, 800], [800, 600], [60, 135]),
    ([0, 0], [1000, 500], [500, 250], [0, 0]),
])
def test_capture_point_maps_ocr_and_dpi_scaling(evaluate, point, source, target, expected):
    arguments = ", ".join(json.dumps(value) for value in (point, source, target))
    assert evaluate(f"mapCapturePoint({arguments})") == expected


def test_capture_point_rounding_cannot_escape_target_image(evaluate):
    # The last source pixel is valid; rounding downscaled coordinates must not
    # create a click exactly at the target's exclusive right/bottom boundary.
    assert evaluate("mapCapturePoint([999,499], [1000,500], [500,250])") == [499, 249]


@pytest.mark.parametrize("arguments", [
    "[NaN,1], [100,100], [50,50]",
    "[1,Infinity], [100,100], [50,50]",
    "[-1,0], [100,100], [50,50]",
    "[100,0], [100,100], [50,50]",
    "[0,100], [100,100], [50,50]",
    "[0,0], [0,100], [50,50]",
    "[0,0], [100,100], [-1,50]",
    "[0,0], [100,NaN], [50,50]",
    "[0,0], [100,100], [50,Infinity]",
    "[0], [100,100], [50,50]",
    "null, [100,100], [50,50]",
    "[0,0], undefined, [50,50]",
    "[0,0], [100,100]",
    "[0,0], [100], [50,50]",
    "[0,0], [100,100], null",
])
def test_capture_point_rejects_unsafe_geometry(evaluate, arguments):
    assert evaluate("""(() => {
  try { mapCapturePoint(%s); return false; } catch (_) { return true; }
})()""" % arguments)
