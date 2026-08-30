const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const formatterSource = fs.readFileSync(
    "static/src/js/inspection_phone_format.js", "utf8"
).replace("export function formatInspectionPhone", "function formatInspectionPhone");
vm.runInThisContext(formatterSource, { filename: "inspection_phone_format.js" });

const cases = [
    ["6625551234", "(662) 555-1234"],
    ["(662)555-1234", "(662) 555-1234"],
    ["1-662-555-1234", "(662) 555-1234"],
    ["+1 662 555 1234", "(662) 555-1234"],
    ["6625551234 x123", "(662) 555-1234 ext. 123"],
    ["662-555-1234 ext. 45", "(662) 555-1234 ext. 45"],
    ["  +44   20 7946 0958  ", "+44 20 7946 0958"],
    ["room 12 extension 345", "room 12 extension 345"],
    ["", ""],
];
for (const [input, expected] of cases) {
    assert.equal(formatInspectionPhone(input), expected, input);
}

const backendSource = fs.readFileSync(
    "static/src/js/inspection_phone_blur_field.js", "utf8"
);
assert.match(backendSource, /onBlur\(event\)/);
assert.match(backendSource, /props\.record\.update/);
assert.doesNotMatch(backendSource, /\.save\s*\(/);

const templateSource = fs.readFileSync(
    "static/src/xml/inspection_phone_blur_field.xml", "utf8"
);
assert.match(templateSource, /<attribute name="t-on-blur">onBlur<\/attribute>/);

console.log("Inspection Contact Phone blur formatting contract: OK");
