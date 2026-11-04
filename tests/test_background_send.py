"""Run the actual injected send branch against a synthetic RichEdit state.

The fake control updates its send cache only through EM_REPLACESEL, reproducing
the distinction that made WM_SETTEXT insufficient on the observed x86 build.
No process attachment, native calls, or UI input occurs in these tests.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE_DIR = Path(__file__).parents[1] / "src" / "kakaocli_win"


@pytest.fixture(scope="module")
def run_send():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the injected send branch")

    def run(**scenario):
        program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const helper = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf("  if(config.op==='send') {");
const end = source.indexOf("  if(config.op==='download') {", start);
if (start < 0 || end <= start) throw Error('Could not locate the actual send branch');
const branch = source.slice(start, end);
const scenario = JSON.parse(process.argv[3]);
const harness = `
${helper}
(async () => {
  const room = 'verified-room-hwnd', edit = 'verified-input-hwnd';
  const config = {op: 'send', room: 'Verified room',
    message: scenario.message === undefined ? 'New message' : scenario.message};
  const events = [];
  const automatic = {mask: 0x40000000, effects: 0x40000000, color: 0};
  const cue = {mask: 0x40000000, effects: 0, color: 0x949494};
  let document = scenario.document === undefined ? '' : scenario.document;
  let selectionFormat = scenario.cue ? cue : automatic;
  let selectedAll = false, cachedSendText = null, virtualFocus;
  const ptr = value => value;
  const Memory = {allocUtf16String: value => ({value})};
  const input = target => {
    if (target !== room) throw Error('Wrong input owner');
    return edit;
  };
  const isEnabled = () => !scenario.disabled;
  const dialogs = () => scenario.modal ? ['blocking-dialog'] : [];
  const text = target => {
    if (target !== room) throw Error('Unexpected caption read');
    return scenario.actualRoom === undefined ? config.room : scenario.actualRoom;
  };
  const richText = target => {
    if (target !== edit) throw Error('Wrong readback control');
    return document;
  };
  const inputText = target => {
    const value = richText(target);
    return isInputCue(value, automatic, selectionFormat) ? '' : value;
  };
  const setText = () => {
    events.push({kind: 'forbidden-set-text'});
    throw Error('WM_SETTEXT does not update the synthetic Kakao send cache');
  };
  const sendMessage = (target, message, wparam, lparam) => {
    if (target !== edit) throw Error('Wrong edit target');
    if (message === 0xb1) {
      if (wparam !== 0 || lparam !== -1) throw Error('Expected complete empty/cue selection');
      selectedAll = true;
      events.push({kind: 'select-all'});
    } else if (message === 0xc2) {
      if (!selectedAll || wparam !== 1 || typeof lparam.value !== 'string') {
        throw Error('Replacement did not use the normal RichEdit edit path');
      }
      cachedSendText = lparam.value;
      document = scenario.readback === undefined ? lparam.value : scenario.readback;
      selectionFormat = automatic;
      events.push({kind: 'replace-selection', text: lparam.value});
    } else {
      throw Error('Unexpected or unsafe edit message: ' + message);
    }
    return ptr(1);
  };
  const key = async (target, keyCode) => {
    if (target !== edit || keyCode !== 13) throw Error('Unexpected key target');
    if (cachedSendText !== config.message) throw Error('Kakao send cache was not updated');
    events.push({kind: 'enter'});
    if (!scenario.keepAfterEnter) document = '';
  };
  const sleep = async () => {};
  let result = null, error = null;
  try { result = await (async () => { ${branch} })(); }
  catch (failure) { error = failure.message; }
  return {result, error, events, document, cachedSendText};
})()
`;
Promise.resolve(vm.runInNewContext(harness, {scenario}))
  .then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [node, "-e", program, str(SOURCE_DIR / "background_agent.js"),
             str(SOURCE_DIR / "win32_abi.js"), json.dumps(scenario, ensure_ascii=True)],
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        return json.loads(completed.stdout)

    return run


@pytest.mark.parametrize("document", ["Existing draft", "메시지 입력"])
def test_existing_real_draft_prevents_every_mutating_action(run_send, document):
    result = run_send(document=document)
    assert result["error"] and "기존 초안" in result["error"]
    assert result["events"] == []
    assert result["document"] == document
    assert result["cachedSendText"] is None


@pytest.mark.parametrize("scenario", [{}, {"document": "메시지 입력", "cue": True}])
def test_empty_or_verified_cue_uses_cache_updating_replace_then_one_enter(run_send, scenario):
    result = run_send(**scenario)
    assert result["error"] is None
    assert [event["kind"] for event in result["events"]] == [
        "select-all", "replace-selection", "enter",
    ]
    assert result["cachedSendText"] == "New message"
    assert result["document"] == ""
    assert result["result"]["submitted"] is True
    assert result["result"]["delivery_confirmed"] is False


def test_normalized_multiline_readback_can_be_submitted(run_send):
    result = run_send(message="Line one\nLine two", readback="Line one\r\nLine two")
    assert result["error"] is None
    assert sum(event["kind"] == "enter" for event in result["events"]) == 1


def test_changed_readback_prevents_enter_without_overwriting_again(run_send):
    result = run_send(readback="Text changed concurrently")
    assert result["error"] and "readback" in result["error"]
    assert [event["kind"] for event in result["events"]] == ["select-all", "replace-selection"]
    assert result["document"] == "Text changed concurrently"


def test_changed_room_title_prevents_enter(run_send):
    result = run_send(actualRoom="Different room")
    assert result["error"] and "Room title changed" in result["error"]
    assert [event["kind"] for event in result["events"]] == ["select-all", "replace-selection"]


@pytest.mark.parametrize("message", ["New message", "메시지 입력"])
def test_pending_text_after_enter_fails_once_without_automatic_retry(run_send, message):
    result = run_send(message=message, keepAfterEnter=True)
    assert result["error"] and "Input remains after Enter" in result["error"]
    assert [event["kind"] for event in result["events"]] == [
        "select-all", "replace-selection", "enter",
    ]
    assert result["document"] == message
    assert result["result"] is None


@pytest.mark.parametrize("scenario", [{"disabled": True}, {"modal": True}])
def test_blocking_window_state_prevents_edit_and_submission(run_send, scenario):
    result = run_send(**scenario)
    assert result["error"] and "modal popup" in result["error"]
    assert result["events"] == []


@pytest.mark.parametrize("message", ["", " ", "\n"])
def test_empty_message_never_selects_or_replaces_input(run_send, message):
    result = run_send(message=message)
    assert result["error"] == "Empty message"
    assert result["events"] == []
