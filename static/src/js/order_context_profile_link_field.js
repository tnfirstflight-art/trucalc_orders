/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import {
    Many2OneField,
    buildM2OFieldDescription,
    extractM2OFieldProps,
} from "@web/views/fields/many2one/many2one_field";


class OrderContextProfileLinkField extends Component {
    static template = "trucalc_orders.OrderContextProfileLinkField";
    static components = { Many2OneField };
    static props = {
        ...Many2OneField.props,
        profileAction: String,
    };

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    get displayName() {
        return this.value?.display_name || "";
    }

    get canOpenProfile() {
        return Boolean(this.props.readonly && this.props.record.resId && this.value);
    }

    get many2OneProps() {
        const { profileAction, ...props } = this.props;
        return props;
    }

    async openProfile() {
        if (!this.canOpenProfile) {
            return;
        }
        const action = await this.orm.call(
            this.props.record.resModel,
            this.props.profileAction,
            [[this.props.record.resId]]
        );
        await this.action.doAction(action);
    }
}

registry.category("fields").add("trucalc_order_context_profile_link", {
    ...buildM2OFieldDescription(OrderContextProfileLinkField),
    extractProps(staticInfo, dynamicInfo) {
        return {
            ...extractM2OFieldProps(staticInfo, dynamicInfo),
            profileAction: staticInfo.options.profile_action,
        };
    },
});
