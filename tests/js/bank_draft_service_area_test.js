const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
    constructor() {
        this.children = [];
        this.dataset = {};
        this.disabled = false;
        this.listeners = {};
        this.value = "";
    }

    replaceChildren() {
        this.children = [];
        this.value = "";
    }

    append(option) {
        this.children.push(option);
        if (option.selected) {
            this.value = option.value;
        }
    }

    addEventListener(name, callback) {
        this.listeners[name] = callback;
    }

    dispatch(name) {
        this.listeners[name]();
    }
}

const state = new FakeElement();
const county = new FakeElement();
const service = new FakeElement();
const areaId = new FakeElement();
const phone = new FakeElement();
phone.value = "6625551234 x123";
const fields = {
    "select[name='service_state_id']": state,
    "select[name='service_county']": county,
    "select[name='service_type']": service,
    "input[name='service_area_id']": areaId,
    "input[name='inspection_contact_phone']": phone,
};
const form = {
    dataset: {
        serviceAreas: JSON.stringify([
            { id: 30, state_id: 45, state_name: "Mississippi", county: "Lee", service_type: "evaluation", service_label: "Evaluation" },
            { id: 31, state_id: 45, state_name: "Mississippi", county: "Pontotoc", service_type: "evaluation", service_label: "Evaluation" },
            { id: 50, state_id: 99, state_name: "Tennessee", county: "Shelby", service_type: "appraisal", service_label: "Appraisal" },
        ]),
    },
    querySelector(selector) {
        return fields[selector];
    },
};

global.document = {
    readyState: "complete",
    createElement() {
        return { value: "", textContent: "", selected: false };
    },
    querySelectorAll(selector) {
        if (selector === ".o_trucalc_bank_draft_upload_form") {
            return [];
        }
        assert.equal(selector, ".o_trucalc_bank_draft_form");
        return [form];
    },
    addEventListener() {
        assert.fail("A late-loaded asset must initialize immediately after DOMContentLoaded");
    },
};

const formatterSource = fs.readFileSync(
    "static/src/js/inspection_phone_format.js", "utf8"
).replace("export function formatInspectionPhone", "function formatInspectionPhone");
vm.runInThisContext(formatterSource, { filename: "inspection_phone_format.js" });
const source = fs.readFileSync(
    "static/src/js/bank_draft_service_area.js", "utf8"
).replace(/^import .*;$/m, "");
vm.runInThisContext(source, { filename: "bank_draft_service_area.js" });

phone.dispatch("blur");
assert.equal(phone.value, "(662) 555-1234 ext. 123");

assert.deepEqual(
    state.children.map((option) => option.textContent),
    ["Select a State", "Mississippi", "Tennessee"]
);
state.value = "45";
state.dispatch("change");
assert.equal(county.disabled, false);
assert.deepEqual(
    county.children.map((option) => option.textContent),
    ["Select a County", "Lee", "Pontotoc"]
);
county.value = "Lee";
county.dispatch("change");
assert.equal(service.disabled, false);
assert.deepEqual(
    service.children.map((option) => option.textContent),
    ["Select a Service", "Evaluation"]
);
service.value = "evaluation";
service.dispatch("change");
assert.equal(areaId.value, "30");
state.value = "99";
state.dispatch("change");
assert.equal(areaId.value, "");
assert.equal(service.disabled, true);

console.log("Bank Draft Service Area browser contract: OK");
