/* JXA adapter. Only app-scoped AX reads, AXValue writes, and AXPress actions. */

function fail(code, message, details) {
    var error = new Error(message);
    error.code = code;
    error.details = details || {};
    throw error;
}

function attempt(fn, fallback) {
    try { return fn(); } catch (_) { return fallback; }
}

function attribute(element, name, fallback) {
    return attempt(function () { return element.attributes.byName(name).value(); }, fallback);
}

function title(element) {
    return String(attribute(element, "AXTitle", attempt(function () { return element.name(); }, "")) || "");
}

function textValue(value) {
    return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function tree(root) {
    var nodes = [], truncated = false, chars = 0;
    function visit(element, path, depth) {
        if (nodes.length >= 1200 || depth > 24 || chars >= 180000) { truncated = true; return; }
        var properties = attempt(function () { return element.properties(); }, {});
        var role = properties.role || attribute(element, "AXRole", "");
        var children = attempt(function () { return element.uiElements(); }, []);
        var node = {
            element: element, path: path, role: String(role),
            title: textValue(properties.name || attribute(element, "AXTitle", "")),
            description: textValue(properties.description || attribute(element, "AXDescription", "")),
            value: textValue(properties.value),
            identifier: "", enabled: properties.enabled !== false,
            editable: false, child_count: children.length
        };
        if (role === "AXTextArea" || role === "AXTextField" || role === "AXButton") {
            node.identifier = textValue(attribute(element, "AXIdentifier", ""));
        }
        if (["AXTextArea", "AXTextField", "AXStaticText", "AXRow", "AXImage"].indexOf(role) >= 0 && !node.value) {
            node.value = textValue(attribute(element, "AXValue", ""));
        }
        if (role === "AXTextArea" || role === "AXTextField") {
            node.editable = attempt(function () { return element.attributes.byName("AXValue").settable(); }, false) === true;
        }
        ["title", "description", "value", "identifier"].forEach(function (key) {
            if (node[key].length > 60000) { node[key] = node[key].slice(0, 60000); truncated = true; }
            chars += node[key].length;
        });
        nodes.push(node);
        for (var i = 0; i < children.length; i++) { visit(children[i], path.concat([i]), depth + 1); }
    }
    visit(root, [], 0);
    return {nodes: nodes, truncated: truncated};
}

function publicNodes(nodes) {
    return nodes.map(function (node) {
        var result = {};
        Object.keys(node).forEach(function (key) { if (key !== "element") { result[key] = node[key]; } });
        return result;
    });
}

function processFor(system) {
    var names = ["KakaoTalk", "카카오톡"], matches = [];
    names.forEach(function (name) {
        var process = system.applicationProcesses.byName(name);
        if (process.exists()) { matches.push(process); }
    });
    if (matches.length !== 1) {
        fail(matches.length ? "ambiguous_process" : "not_running", "Exactly one running KakaoTalk process is required. Launch and log in manually.");
    }
    return matches[0];
}

function context(system) {
    var process = processFor(system), windows = process.windows(), modals = [];
    windows.forEach(function (window) {
        var windowTitle = title(window);
        var subrole = attribute(window, "AXSubrole", "");
        if (subrole === "AXDialog" || subrole === "AXSystemDialog" || attribute(window, "AXModal", false) === true) {
            modals.push({element: window, parent: null, title: windowTitle, kind: "dialog"});
        }
        attempt(function () { return window.sheets(); }, []).forEach(function (sheet) {
            modals.push({element: sheet, parent: windowTitle, title: title(sheet), kind: "sheet"});
        });
    });
    return {process: process, windows: windows, modals: modals};
}

function noModal(state) {
    if (state.modals.length) {
        fail("pending_modal", "Resolve the app-owned dialog first; only download can act on an existing Save sheet.", {
            pending_modals: state.modals.map(function (modal) { return {title: modal.title, parent: modal.parent, kind: modal.kind}; })
        });
    }
}

function exactWindow(state, name) {
    if (!name || !String(name).trim()) { fail("room_required", "An exact chat window title is required."); }
    var matches = state.windows.filter(function (window) { return title(window) === name; });
    if (matches.length !== 1) { fail("room_not_unique", "Expected one already open window with the exact requested title.", {room: name, matched_windows: matches.length}); }
    return matches[0];
}

function unique(nodes, predicate, code, message) {
    var matches = nodes.filter(predicate);
    if (matches.length !== 1) { fail(code, message, {matched_controls: matches.length}); }
    return matches[0];
}

function label(node) { return [node.title, node.description, node.identifier].join(" ").trim(); }

function requirePress(node) {
    if (!node.element.actions.byName("AXPress").exists()) { fail("axpress_unavailable", "The control does not expose AXPress."); }
}

function press(node) {
    if (attribute(node.element, "AXEnabled", node.enabled) === false) { fail("control_disabled", "The requested AX control is disabled."); }
    var action = node.element.actions.byName("AXPress");
    requirePress(node);
    action.perform();
}

function setValue(node, value, expected) {
    var current = textValue(attribute(node.element, "AXValue", ""));
    if (current !== expected) { fail("control_changed", "The editable value changed after preflight; it was left untouched."); }
    node.element.attributes.byName("AXValue").value = value;
    if (textValue(attribute(node.element, "AXValue", "")) !== value) {
        fail("value_not_accepted", "The app did not retain the requested AXValue; no button was pressed.", {draft_preserved: true});
    }
}

function send(request, system, state) {
    noModal(state);
    var window = exactWindow(state, request.room), snapshot = tree(window);
    if (snapshot.truncated) { fail("tree_truncated", "The AX tree exceeded its inspection bound; no message was entered."); }
    var composer = unique(snapshot.nodes, function (node) {
        return node.editable && (node.role === "AXTextArea" || (node.role === "AXTextField" && /message|compose|메시지|대화.*입력/i.test(label(node))));
    }, "composer_not_unique", "A unique editable chat composer was not exposed by KakaoTalk.");
    var button = unique(snapshot.nodes, function (node) {
        return node.role === "AXButton" && [node.title, node.description].some(function (value) { return /^(?:보내기|전송|메시지 전송|send|send message)$/i.test(value.trim()); });
    }, "send_button_not_unique", "A unique named Send button is required; keyboard fallback is disabled.");
    requirePress(button);
    if (composer.value.length) { fail("existing_draft", "The chat already contains a draft; it was preserved.", {draft_preserved: true}); }
    if (!request.message || !request.message.trim() || request.message.indexOf("\0") >= 0 || request.message.length > 60000) {
        fail("invalid_message", "Message must be nonempty, contain no NUL, and be at most 60000 characters.");
    }
    var result = {status: "preview", room: request.room, message: request.message, submitted: false, background_only: true};
    if (!request.yes) { return result; }
    // Recheck app modal state before every operation that can change the chat.
    noModal(context(system));
    exactWindow(context(system), request.room);
    setValue(composer, request.message, composer.value);
    noModal(context(system));
    if (title(window) !== request.room || textValue(attribute(composer.element, "AXValue", "")) !== request.message) {
        fail("chat_changed", "Chat or draft changed before Send; no button was pressed.", {draft_preserved: true});
    }
    try { press(button); }
    catch (error) { error.details = Object.assign(error.details || {}, {draft_preserved: true, side_effect_uncertain: true}); throw error; }
    return {status: "submitted_unconfirmed", room: request.room, submitted: true, delivery_confirmed: false, background_only: true};
}

function download(request, system, state) {
    if (state.modals.length !== 1) { fail("save_dialog_not_unique", "Exactly one existing KakaoTalk Save sheet/dialog is required. This command never opens one."); }
    var modal = state.modals[0];
    if (request.room && modal.parent !== request.room) {
        fail("sheet_room_mismatch", "The pending Save sheet is not attached to the requested exact chat window.");
    }
    if (request.room) { exactWindow(state, request.room); }
    var snapshot = tree(modal.element);
    if (snapshot.truncated) { fail("tree_truncated", "Save dialog AX tree exceeded its inspection bound."); }
    var save = unique(snapshot.nodes, function (node) {
        return node.role === "AXButton" && [node.title, node.description].some(function (value) { return /^(?:저장|save)$/i.test(value.trim()); });
    }, "save_button_not_unique", "A unique named Save button is required; replacement and confirmation dialogs are not automated.");
    requirePress(save);
    var fields = snapshot.nodes.filter(function (node) { return node.role === "AXTextField" && node.editable; });
    var edits = [];
    if (request.output) {
        var fullPath = fields.filter(function (node) { return /(?:full.?path|file.?path|전체.*경로|파일.*경로)/i.test(label(node)); });
        if (fullPath.length === 1) { edits.push({node: fullPath[0], value: request.output}); }
        else {
            var directory = unique(fields, function (node) { return /^(?:directory|folder|directorypath|folderpath|폴더|디렉터리)$/i.test(node.identifier || node.title || node.description); }, "path_control_unavailable", "This Save dialog exposes no unique editable absolute path. Navigate manually to the folder and use --filename instead.");
            var filename = unique(fields, function (node) { return /(?:saveasname|filename|save.?as|이름|파일명)/i.test(label(node)); }, "filename_control_unavailable", "The Save dialog exposes no unique filename field.");
            if (directory === filename) { fail("path_control_ambiguous", "Directory and filename controls must be distinct."); }
            var slash = request.output.lastIndexOf("/");
            if (slash < 0) { fail("absolute_path_required", "Output must be an absolute POSIX path."); }
            edits.push({node: directory, value: request.output.slice(0, slash) || "/"}, {node: filename, value: request.output.slice(slash + 1)});
        }
    } else {
        var field = unique(fields, function (node) { return /(?:saveasname|filename|save.?as|이름|파일명)/i.test(label(node)); }, "filename_control_unavailable", "The Save dialog exposes no unique filename field.");
        if (!request.filename || /[\/\\\0:]/.test(request.filename) || request.filename === "." || request.filename === "..") {
            fail("invalid_filename", "Use a single filename, without directory separators.");
        }
        edits.push({node: field, value: request.filename});
    }
    var result = {status: "preview", save_requested: false, background_only: true, room: request.room || null, room_verified: Boolean(request.room), output: request.output || null, filename: request.filename || null, directory_verified: Boolean(request.output)};
    if (!request.yes) { return result; }
    // Re-read the same owned sheet; unrelated app-modal UI blocks all writes.
    var current = context(system);
    if (current.modals.length !== 1 || current.modals[0].parent !== modal.parent || current.modals[0].title !== modal.title) {
        fail("save_dialog_changed", "The pending app-owned dialog changed; it was left untouched.");
    }
    edits.forEach(function (edit) { setValue(edit.node, edit.value, edit.node.value); });
    try { press(save); }
    catch (error) { error.details = Object.assign(error.details || {}, {side_effect_uncertain: true}); throw error; }
    result.status = "save_requested_unconfirmed";
    result.save_requested = true;
    result.download_confirmed = false;
    return result;
}

function execute(request, system) {
    var operation = request.operation;
    if (["doctor", "windows", "rooms", "snapshot", "send", "download"].indexOf(operation) < 0) { fail("unknown_operation", "Unknown AX operation."); }
    var state;
    try { state = context(system); }
    catch (error) {
        if (operation === "doctor" && error.code === "not_running") {
            return {status: "ok", platform: "macos", running: false, background_only: true, live_macos_verified: false, note: error.message};
        }
        throw error;
    }
    if (operation === "doctor") {
        return {status: "ok", platform: "macos", running: true, accessibility_probe: "windows_readable", window_count: state.windows.length, pending_modal_count: state.modals.length, background_only: true, live_macos_verified: false};
    }
    if (operation === "windows" || operation === "rooms") {
        var windows = state.windows.map(function (window) { return {title: title(window), subrole: attribute(window, "AXSubrole", "")}; });
        if (operation === "rooms") { windows = windows.filter(function (window) { return window.title && !/^(?:KakaoTalk|카카오톡|로그인|login)$/i.test(window.title) && window.subrole !== "AXDialog" && window.subrole !== "AXSystemDialog"; }); }
        return {status: "ok", windows: windows, coverage: "already_open_window_titles_only", pending_modal_count: state.modals.length, background_only: true};
    }
    if (operation === "download") { return download(request, system, state); }
    if (operation === "send") { return send(request, system, state); }
    noModal(state);
    var window = exactWindow(state, request.room), snapshot = tree(window);
    return {status: "ok", room: request.room, nodes: publicNodes(snapshot.nodes), truncated: snapshot.truncated, background_only: true};
}

function run() {
    ObjC.import("Foundation");
    try {
        var data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
        var input = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
        return JSON.stringify(execute(JSON.parse(input), Application("System Events")));
    } catch (error) {
        var result = {status: "error", error: {code: error.code || "accessibility_error", message: String(error.message || error)}};
        Object.keys(error.details || {}).forEach(function (key) { result[key] = error.details[key]; });
        return JSON.stringify(result);
    }
}
