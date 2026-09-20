/** @odoo-module **/

import { session } from "@web/session";
import { patch } from "@web/core/utils/patch";
import { SwitchCompanyMenu } from "@web/webclient/switch_company_menu/switch_company_menu";

patch(SwitchCompanyMenu.prototype, {
    get trucalcCompanySelectorVisible() {
        return !session.trucalc_hide_company_selector;
    },
});
