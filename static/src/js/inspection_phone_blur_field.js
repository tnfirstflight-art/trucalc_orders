/** @odoo-module **/

import { registry } from "@web/core/registry";
import { PhoneField, phoneField } from "@web/views/fields/phone/phone_field";
import { formatInspectionPhone } from "./inspection_phone_format";


class InspectionPhoneBlurField extends PhoneField {
    static template = "trucalc_orders.InspectionPhoneBlurField";

    onBlur(event) {
        const formatted = formatInspectionPhone(event.currentTarget.value);
        event.currentTarget.value = formatted;
        if (formatted !== (this.props.record.data[this.props.name] || "")) {
            this.props.record.update({ [this.props.name]: formatted });
        }
    }
}

registry.category("fields").add("inspection_phone_blur", {
    ...phoneField,
    component: InspectionPhoneBlurField,
});
