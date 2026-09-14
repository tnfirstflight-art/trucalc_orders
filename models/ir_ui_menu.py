from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    _TRUCALC_COMMON_HIDDEN_LAUNCHER_XMLIDS = (
        "project_todo.menu_todo_todos",
        "contacts.menu_contacts",
        "project.menu_main_pm",
        "hr.menu_hr_root",
    )
    _TRUCALC_OPERATIONS_HIDDEN_LAUNCHER_XMLIDS = (
        "base.menu_management",
        "base.menu_administration",
    )

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible_ids = super()._visible_menu_ids(debug)
        membership = self.env.user._trucalc_persona_membership()
        internal_roles = membership["internal"]
        if not internal_roles:
            return visible_ids

        admin_group = self.env.ref("trucalc_orders.group_trucalc_admin")
        clean_admin = (
            internal_roles == admin_group
            and not membership["bank"]
            and not membership["vendor"]
        )
        hidden_xmlids = self._TRUCALC_COMMON_HIDDEN_LAUNCHER_XMLIDS
        if not clean_admin:
            hidden_xmlids += self._TRUCALC_OPERATIONS_HIDDEN_LAUNCHER_XMLIDS

        hidden_ids = {
            menu.id
            for xmlid in hidden_xmlids
            if (menu := self.env.ref(xmlid, raise_if_not_found=False))
        }
        return visible_ids.difference(hidden_ids)

    @api.model
    def _load_menus_blacklist(self):
        blacklist = list(super()._load_menus_blacklist())
        if self.env.user._trucalc_is_restricted_internal():
            for xmlid in (
                "mail.menu_root_discuss",
                "project_todo.menu_todo_todos",
                "contacts.menu_contacts",
                "base.menu_management",
            ):
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu:
                    blacklist.append(menu.id)
        return blacklist
