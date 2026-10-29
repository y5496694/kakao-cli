/* Mock System Events object model: validates adapter logic, not macOS APIs. */
const fs = require("fs");
const vm = require("vm");
const fixture = JSON.parse(fs.readFileSync(0, "utf8"));
const events = [];

function element(spec) {
    const children = (spec.children || []).map(element);
    const sheets = (spec.sheets || []).map(element);
    const result = {
        name: () => spec.title || "",
        properties: () => ({role: spec.role || "AXWindow", name: spec.title || "", description: spec.description || "", value: spec.value || "", enabled: spec.enabled !== false}),
        uiElements: () => children,
        sheets: () => sheets,
        attributes: {byName(name) {
            const attribute = {settable: () => Boolean(spec.editable)};
            Object.defineProperty(attribute, "value", {
                get() { return () => {
                    const values = {AXRole: spec.role || "AXWindow", AXTitle: spec.title || "", AXSubrole: spec.subrole || "", AXDescription: spec.description || "", AXModal: Boolean(spec.modal), AXValue: spec.value || "", AXIdentifier: spec.identifier || "", AXEnabled: spec.enabled !== false};
                    return values[name];
                }; },
                set(value) { spec.value = value; events.push({operation: "set_value", identifier: spec.identifier || "", value}); }
            });
            return attribute;
        }},
        actions: {byName(name) {
            return {
                exists: () => name === "AXPress" && spec.press !== false,
                perform: () => { events.push({operation: "press", title: spec.title || ""}); if (spec.pressError) { throw new Error("Press outcome unknown"); } }
            };
        }}
    };
    return result;
}

const windows = (fixture.windows || []).map(element);
const process = {exists: () => fixture.running !== false, windows: () => windows};
const absent = {exists: () => false};
const system = {applicationProcesses: {byName: name => name === "KakaoTalk" ? process : absent}};
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(fixture.source, "utf8"), context);
try {
    const result = context.execute(fixture.request, system);
    console.log(JSON.stringify({result, events}));
} catch (error) {
    console.log(JSON.stringify({error: {code: error.code || "error", message: error.message, details: error.details || {}}, events}));
}
