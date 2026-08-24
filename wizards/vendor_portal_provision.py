from odoo import Command, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError


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
    vendor_email = fields.Char(
        string="Vendor Email",
        related="vendor_id.email",
        readonly=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Portal User",
        domain=[
            ("active", "=", True),
            ("share", "=", True),
            ("trucalc_bank_company_id", "=", False),
            ("trucalc_vendor_id", "=", False),
        ],
    )

    def _check_actor(self):
        if not self.env.user.has_group("trucalc_orders.group_trucalc_admin"):
            raise AccessError(_("Only TruCalc Administrators may provision vendor users."))

    def _success_notification(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Vendor Portal User Provisioned"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _normalized_vendor_email(self):
        email = tools.email_normalize(self.vendor_id.email)
        if not email:
            raise ValidationError(_("The vendor must have one valid email address."))
        return email

    def _resolve_email_identity(self, email):
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        Partners = self.env["res.partner"].sudo().with_context(active_test=False)
        users = Users.search([
            "|",
            ("login", "=ilike", email),
            ("partner_id.email", "=ilike", email),
        ])
        partners = Partners.search([("email", "=ilike", email)]) | users.partner_id

        if len(users) > 1:
            raise ValidationError(_(
                "More than one user matches the vendor email. Resolve the identity collision before provisioning."
            ))
        if len(partners) > 1:
            raise ValidationError(_(
                "More than one contact matches the vendor email. Resolve the identity collision before provisioning."
            ))
        if users and partners and users.partner_id != partners:
            raise ValidationError(_(
                "The vendor email matches unrelated contact and user identities. Resolve the collision before provisioning."
            ))
        return partners, users

    def _assert_reusable_plain_portal(self, user):
        groups = user.all_group_ids
        persona = user._trucalc_persona_membership()
        if (
            not user.active
            or not user.share
            or self.env.ref("base.group_portal") not in groups
            or self.env.ref("base.group_user") in groups
            or persona["internal"]
            or persona["bank"]
            or persona["vendor"]
            or user.trucalc_bank_company_id
            or user.trucalc_vendor_id
        ):
            raise ValidationError(_(
                "The identity matching the vendor email is not an eligible plain portal user."
            ))

    def action_provision(self):
        self.ensure_one()
        self._check_actor()
        if not self.user_id:
            raise ValidationError(_("Select an existing Portal User or use Create and Invite Portal User."))
        self.user_id._trucalc_provision_vendor_portal(self.vendor_id, self.env.user)
        return self._success_notification(
            _("The existing portal user was mapped to %s. No Order authorization was created.")
            % self.vendor_id.display_name
        )

    def action_create_and_invite(self):
        self.ensure_one()
        self._check_actor()
        email = self._normalized_vendor_email()
        partner, user = self._resolve_email_identity(email)

        if user:
            self._assert_reusable_plain_portal(user)
            user._trucalc_provision_vendor_portal(self.vendor_id, self.env.user)
            return self._success_notification(
                _("The existing portal identity was mapped to %s. No duplicate user was created.")
                % self.vendor_id.display_name
            )

        if partner and not partner.active:
            raise ValidationError(_(
                "The contact matching the vendor email is archived. Resolve it before provisioning."
            ))
        if not partner:
            # TruCalc Administrators intentionally do not receive Odoo's broad
            # Contact/Creation role.  Elevate only this resolved identity and
            # the standard portal transient records after all collision checks.
            partner = self.env["res.partner"].sudo().create({
                "name": self.vendor_id.name,
                "email": email,
            })

        portal_wizard = self.env["portal.wizard"].sudo().create({
            "partner_ids": [Command.set(partner.ids)],
        })
        portal_line = portal_wizard.user_ids
        portal_line.ensure_one()
        portal_line.email = email
        portal_line._assert_user_email_uniqueness()
        if portal_line.user_id:
            raise ValidationError(_(
                "The contact already has a user identity that cannot be provisioned automatically."
            ))

        user = portal_line._create_user()
        user.sudo().write({
            "active": True,
            "group_ids": [
                Command.link(self.env.ref("base.group_portal").id),
                Command.unlink(self.env.ref("base.group_public").id),
            ],
        })
        user.partner_id.signup_prepare()
        user._trucalc_provision_vendor_portal(self.vendor_id, self.env.user)

        portal_line.user_id = user
        portal_line.with_context(active_test=True)._send_email()
        return self._success_notification(
            _("A portal invitation was sent and the user was mapped to %s. No Order authorization was created.")
            % self.vendor_id.display_name
        )
