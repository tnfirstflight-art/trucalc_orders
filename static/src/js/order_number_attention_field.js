/** @odoo-module **/

import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";


class OrderNumberAttentionField extends CharField {
    static template = "trucalc_orders.OrderNumberAttentionField";

    get attentionLabel() {
        return this.props.record.data.engagement_action_required_label || "";
    }
}

registry.category("fields").add("trucalc_order_number_attention", {
    ...charField,
    component: OrderNumberAttentionField,
});
