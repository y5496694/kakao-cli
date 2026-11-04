"""Synthetic regression tests for the observed x86 RichEdit cue signature.

The same words can be a real draft; matching their text alone must never allow
the caller to overwrite it. These tests execute only the pure JavaScript helper.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


HELPER = Path(__file__).parents[1] / "src" / "kakaocli_win" / "win32_abi.js"
COLOR = 0x40000000  # CFM_COLOR in masks, CFE_AUTOCOLOR in effects.
DEFAULT = {"mask": COLOR, "effects": COLOR, "color": 0}
CUE = {"mask": COLOR, "effects": 0, "color": 0x949494}


@pytest.fixture(scope="module")
def is_cue():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the input cue helper")

    def evaluate(text="메시지 입력", default=DEFAULT, selection=CUE):
        arguments = ",".join(json.dumps(v, ensure_ascii=True) for v in (text, default, selection))
        program = """
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const value = vm.runInNewContext(source + '\\nisInputCue(' + process.argv[2] + ')');
process.stdout.write(JSON.stringify(value));
"""
        completed = subprocess.run(
            [node, "-e", program, str(HELPER), arguments],
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        return json.loads(completed.stdout)

    return evaluate


def test_observed_exact_gray_cue_with_automatic_black_default(is_cue):
    assert is_cue() is True


@pytest.mark.parametrize("selection", [
    DEFAULT,
    {"mask": COLOR, "effects": 0, "color": 0},
])
def test_identical_black_user_draft_is_preserved(is_cue, selection):
    assert is_cue(selection=selection) is False


@pytest.mark.parametrize("text", [
    "다른 초안", "테스트", "메시지 입력 ", " 메시지 입력", "메시지 입력\n",
    "메시지\u00a0입력", "", None,
])
def test_gray_text_still_requires_the_exact_observed_cue(is_cue, text):
    # Empty text is already an empty document, not a detected cue.
    assert is_cue(text=text) is False


@pytest.mark.parametrize("field", ["mask", "effects", "color"])
@pytest.mark.parametrize("which", ["default", "selection"])
def test_missing_format_attributes_never_identify_a_cue(is_cue, field, which):
    default, selection = dict(DEFAULT), dict(CUE)
    (default if which == "default" else selection).pop(field)
    assert is_cue(default=default, selection=selection) is False


@pytest.mark.parametrize("which", ["default", "selection"])
@pytest.mark.parametrize("mask", [0, 1, 0xBFFFFFFF])
def test_missing_or_mixed_color_mask_is_ambiguous(is_cue, which, mask):
    # EM_GETCHARFORMAT omits CFM_COLOR when selected text has mixed colors.
    default, selection = dict(DEFAULT), dict(CUE)
    (default if which == "default" else selection)["mask"] = mask
    assert is_cue(default=default, selection=selection) is False


def test_selection_automatic_color_is_a_real_draft(is_cue):
    assert is_cue(selection={**CUE, "effects": COLOR}) is False


def test_default_must_be_automatic_black(is_cue):
    assert is_cue(default={**DEFAULT, "effects": 0}) is False
    assert is_cue(default={**DEFAULT, "color": 0x949494}) is False


@pytest.mark.parametrize("color", [0, 0x939393, 0x959595, 0xFFFFFF])
def test_other_explicit_selection_colors_are_not_the_observed_cue(is_cue, color):
    assert is_cue(selection={**CUE, "color": color}) is False


@pytest.mark.parametrize("unsupported", [None, {}, "unsupported", [], 1])
@pytest.mark.parametrize("which", ["default", "selection"])
def test_unsupported_format_values_are_rejected(is_cue, which, unsupported):
    kwargs = {which: unsupported}
    assert is_cue(**kwargs) is False


@pytest.mark.parametrize("field,value", [
    ("mask", str(COLOR)), ("effects", None), ("effects", 0.5),
    ("color", float("nan")), ("color", float("inf")),
])
def test_noninteger_format_fields_are_not_coerced(is_cue, field, value):
    assert is_cue(selection={**CUE, field: value}) is False
