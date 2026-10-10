/** @odoo-module **/

import { CharField, charField } from "@web/views/fields/char/char_field";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";


class InternalUserManageLinkField extends CharField {
    static template = "trucalc_orders.InternalUserManageLinkField";

    setup() {
        super.setup();
        this.action = useService("action");
        this.orm = useService("orm");
    }

    get canManage() {
        return Boolean(this.props.readonly && this.props.record.resId);
    }

    async openManagement() {
        if (!this.canManage) {
            return;
        }
        const action = await this.orm.call(
            this.props.record.resModel,
            "action_manage",
            [[this.props.record.resId]]
        );
        await this.action.doAction(action);
    }
}

registry.category("fields").add("trucalc_internal_user_manage_link", {
    ...charField,
    component: InternalUserManageLinkField,
});
