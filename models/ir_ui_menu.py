from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

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
