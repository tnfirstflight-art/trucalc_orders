const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const draftForm = { id: "o_trucalc_bank_draft_form" };
const generated = [];
const uploadForm = {
    dataset: { draftFormId: draftForm.id },
    listeners: {},
    querySelectorAll(selector) {
        assert.equal(selector, "input[data-draft-field]");
        return generated;
    },
    addEventListener(name, callback) {
        this.listeners[name] = callback;
    },
    append(input) {
        generated.push(input);
    },
};

global.FormData = class FormData {
    constructor(form) {
        assert.equal(form, draftForm);
    }

    *[Symbol.iterator]() {
        yield ["csrf_token", "ignored"];
        yield ["draft_action", "save"];
        yield ["borrower", "Current Unsaved Borrower"];
        yield ["inspection_contact_email", ""];
        yield ["service_area_id", "30"];
    }
};

global.document = {
    readyState: "complete",
    querySelectorAll(selector) {
        if (selector === ".o_trucalc_bank_draft_form") {
            return [];
        }
        assert.equal(selector, ".o_trucalc_bank_draft_upload_form");
        return [uploadForm];
    },
    getElementById(id) {
        assert.equal(id, draftForm.id);
        return draftForm;
    },
    createElement(tag) {
        assert.equal(tag, "input");
        return { dataset: {} };
    },
};

const source = fs.readFileSync(
    "static/src/js/bank_draft_service_area.js", "utf8"
).replace(/^import .*;$/m, "");
vm.runInThisContext(source, { filename: "bank_draft_service_area.js" });
uploadForm.listeners.submit();

assert.deepEqual(generated.map((input) => [input.name, input.value]), [
    ["borrower", "Current Unsaved Borrower"],
    ["inspection_contact_email", ""],
    ["service_area_id", "30"],
]);
assert.ok(generated.every((input) => input.type === "hidden"));
assert.ok(generated.every((input) => input.dataset.draftField === "true"));

console.log("Bank Draft action isolation browser contract: OK");
