/** @odoo-module **/

import { registry } from "@web/core/registry";
import {
    buildM2OFieldDescription,
    Many2OneField,
} from "@web/views/fields/many2one/many2one_field";


class Many2OneAutosaveField extends Many2OneField {
    get m2oProps() {
        const props = super.m2oProps;
        return {
            ...props,
            update: (value) => props.update(value, { save: true }),
        };
    }
}

registry.category("fields").add(
    "many2one_autosave",
    buildM2OFieldDescription(Many2OneAutosaveField)
);
