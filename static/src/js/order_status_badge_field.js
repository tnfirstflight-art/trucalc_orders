/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { BadgeField, badgeField } from "@web/views/fields/badge/badge_field";


class OrderStatusBadgeField extends BadgeField {
    static template = "trucalc_orders.OrderStatusBadgeField";

    get outcome() {
        return this.props.record.data.internal_fee_change_outcome || "";
    }

    get outcomeLabel() {
        return {
            approved: _t("Fee Change Approved"),
            declined: _t("Fee Change Declined"),
        }[this.outcome] || "";
    }

    get outcomeBadgeClass() {
        return this.outcome === "approved" ? "text-bg-success" : "text-bg-danger";
    }
}

registry.category("fields").add("trucalc_order_status_badge", {
    ...badgeField,
    component: OrderStatusBadgeField,
});
