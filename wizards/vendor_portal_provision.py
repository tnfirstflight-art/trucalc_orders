from odoo import fields, models, _
from odoo.exceptions import AccessError


class TruCalcVendorPortalProvision(models.TransientModel):
    _name = "trucalc.vendor.portal.provision"
    _description = "Provision TruCalc Vendor Portal User"

    vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Vendor",
        required=True,
        readonly=True,
        domain=[("active", "=", True)],
    )
    user_id = fields.Many2one(
        "res.users",
        string="Portal User",
        required=True,
        domain=[
            ("active", "=", True),
            ("share", "=", True),
            ("trucalc_bank_company_id", "=", False),
            ("trucalc_vendor_id", "=", False),
        ],
    )

    def action_provision(self):
        self.ensure_one()
        actor = self.env.user
        if not actor.has_group("trucalc_orders.group_trucalc_admin"):
            raise AccessError(_("Only TruCalc Administrators may provision vendor users."))
        self.user_id._trucalc_provision_vendor_portal(self.vendor_id, actor)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Vendor Portal User Provisioned"),
                "message": _("The portal user was mapped to %s. No Order authorization was created.") % self.vendor_id.display_name,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
