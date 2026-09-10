import re

from odoo import Command, api, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError


BANK_PROFILE_FIELDS = frozenset({
    "name", "currency_id", "street", "street2", "city", "state_id",
    "zip", "country_id", "phone", "email", "website", "logo",
})


class ResCompany(models.Model):
    _inherit = "res.company"

    trucalc_is_bank = fields.Boolean(
        string="TruCalc Bank",
        default=False,
        index=True,
        copy=False,
    )
    trucalc_bank_active = fields.Boolean(
        string="Bank Active",
        default=False,
        index=True,
        copy=False,
        help="Controls TruCalc Bank portal authorization independently of company archival.",
    )
    trucalc_bank_user_count = fields.Integer(
        string="Bank Users",
        compute="_compute_trucalc_bank_user_count",
        compute_sudo=True,
    )
    trucalc_bank_admin_audit_ids = fields.One2many(
        "trucalc.bank.admin.audit",
        "bank_company_id",
        string="Administration History",
        readonly=True,
    )

    @api.depends("user_ids.trucalc_bank_company_id")
    def _compute_trucalc_bank_user_count(self):
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        for company in self:
            company.trucalc_bank_user_count = Users.search_count([
                ("trucalc_bank_company_id", "=", company.id),
            ]) if company.id else 0

    @api.model
    def _trucalc_normalize_bank_name(self, name):
        if not isinstance(name, str):
            return ""
        return re.sub(r"\s+", " ", name).strip()

    @api.model
    def _trucalc_require_bank_administrator(self):
        actor = self.env.user
        membership = actor._trucalc_persona_membership()
        if (
            not actor.active
            or actor.share
            or len(membership["internal"]) != 1
            or not actor.has_group("trucalc_orders.group_trucalc_admin")
            or membership["bank"]
            or membership["vendor"]
            or actor.trucalc_bank_company_id
            or actor.trucalc_vendor_id
            or self.env.ref("base.group_user") not in actor.sudo().all_group_ids
        ):
            raise AccessError(_("Only TruCalc Administrators may manage Banks."))
        return actor

    @api.model
    def _trucalc_prepare_bank_profile(self, values, current=False):
        if not isinstance(values, dict) or set(values) - BANK_PROFILE_FIELDS:
            raise AccessError(_("The Bank profile contains unsupported fields."))
        prepared = {key: value for key, value in values.items() if key in BANK_PROFILE_FIELDS}
        name = self._trucalc_normalize_bank_name(
            prepared.get("name", current.name if current else "")
        )
        if not name:
            raise ValidationError(_("Bank Name is required."))
        duplicate_domain = [("name", "=ilike", name)]
        if current:
            duplicate_domain.append(("id", "!=", current.id))
        if self.sudo().with_context(active_test=False).search_count(duplicate_domain):
            raise ValidationError(_("A company with this Bank Name already exists."))
        prepared["name"] = name

        currency_id = prepared.get(
            "currency_id", current.currency_id.id if current else False
        )
        currency = self.env["res.currency"].sudo().browse(currency_id).exists()
        if len(currency) != 1:
            raise ValidationError(_("Currency is required."))
        prepared["currency_id"] = currency.id
        return prepared

    @api.model
    def _trucalc_internal_company_users(self):
        groups = self.env["res.groups"].browse([
            self.env.ref("trucalc_orders.group_trucalc_admin").id,
            self.env.ref("trucalc_orders.group_trucalc_operations").id,
        ])
        candidates = self.env["res.users"].sudo().search([
            ("active", "=", True),
            ("share", "=", False),
            ("group_ids", "in", groups.ids),
        ])
        return candidates.filtered(lambda user: (
            len(user._trucalc_persona_membership()["internal"]) == 1
            and not user._trucalc_persona_membership()["bank"]
            and not user._trucalc_persona_membership()["vendor"]
            and not user.trucalc_bank_company_id
            and not user.trucalc_vendor_id
            and self.env.ref("base.group_user") in user.all_group_ids
        ))

    @api.model
    @api.private
    def _trucalc_create_bank(self, values):
        actor = self._trucalc_require_bank_administrator()
        prepared = self._trucalc_prepare_bank_profile(values)
        prepared.update({
            "parent_id": False,
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        with self.env.cr.savepoint():
            bank = super(ResCompany, self.sudo()).create(prepared)
            if bank == self.env.ref("base.main_company"):
                raise ValidationError(_("The TruCalc main company cannot be a Bank."))
            internal_users = self._trucalc_internal_company_users()
            if internal_users:
                internal_users.write({
                    "company_ids": [Command.link(bank.id)],
                })
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "bank_created", actor, bank,
                prior_status=False, new_status="active",
                metadata={"currency_id": bank.currency_id.id},
            )
        return bank

    @api.private
    def _trucalc_update_bank_profile(self, values):
        self.ensure_one()
        self._trucalc_require_bank_administrator()
        bank = self.sudo().exists()
        if (
            len(bank) != 1
            or not bank.trucalc_is_bank
            or bank == self.env.ref("base.main_company")
        ):
            raise AccessError(_("The Bank profile is not authorized."))
        prepared = self._trucalc_prepare_bank_profile(values, current=bank)
        super(ResCompany, bank).write(prepared)
        return bank

    @api.private
    def _trucalc_bank_identity_record(self, require_active=True):
        self.ensure_one()
        bank = self.sudo().exists()
        if (
            len(bank) != 1
            or not bank.trucalc_is_bank
            or bank == self.env.ref("base.main_company")
            or (require_active and not bank.trucalc_bank_active)
        ):
            raise AccessError(_("The TruCalc Bank is not authorized."))
        return bank

    def action_trucalc_deactivate_bank(self):
        self.ensure_one()
        actor = self._trucalc_require_bank_administrator()
        bank = self._trucalc_bank_identity_record()
        with self.env.cr.savepoint():
            bank.flush_recordset(["trucalc_bank_active"])
            self.env.cr.execute(
                "SELECT id FROM res_company WHERE id = %s FOR UPDATE", (bank.id,),
            )
            bank.invalidate_recordset(["trucalc_bank_active"])
            bank._trucalc_bank_identity_record()
            mapped_users = self.env["res.users"].sudo().with_context(
                active_test=False
            ).search([
                ("trucalc_bank_company_id", "=", bank.id),
                ("active", "=", True),
            ])
            super(ResCompany, bank).write({"trucalc_bank_active": False})
            for user in mapped_users:
                user.write({"active": False})
                self.env["trucalc.bank.admin.audit"]._trucalc_log(
                    "bank_user_deactivated_by_bank_deactivation",
                    actor,
                    bank,
                    target_user=user,
                    prior_status="active",
                    new_status="inactive",
                    metadata={"login": (user.login or "").strip().casefold()},
                )
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "bank_deactivated", actor, bank,
                prior_status="active", new_status="inactive",
                metadata={"deactivated_user_count": len(mapped_users)},
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_trucalc_activate_bank(self):
        self.ensure_one()
        actor = self._trucalc_require_bank_administrator()
        bank = self._trucalc_bank_identity_record(require_active=False)
        if bank.trucalc_bank_active:
            raise ValidationError(_("The Bank is already active."))
        with self.env.cr.savepoint():
            bank.flush_recordset(["trucalc_bank_active"])
            self.env.cr.execute(
                "SELECT id FROM res_company WHERE id = %s FOR UPDATE", (bank.id,),
            )
            bank.invalidate_recordset(["trucalc_bank_active"])
            if bank.trucalc_bank_active:
                raise ValidationError(_("The Bank is already active."))
            super(ResCompany, bank).write({"trucalc_bank_active": True})
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "bank_activated", actor, bank,
                prior_status="inactive", new_status="active",
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_trucalc_edit_bank(self):
        self.ensure_one()
        self._trucalc_require_bank_administrator()
        bank = self._trucalc_bank_identity_record(require_active=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("Edit Bank"),
            "res_model": "trucalc.bank.provision",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bank_company_id": bank.id},
        }

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("trucalc_is_bank") or vals.get("trucalc_bank_active") for vals in vals_list):
            if tools.config["test_enable"] and self.env.context.get(
                "trucalc_test_bank_fixture"
            ):
                return super().create(vals_list)
            raise AccessError(_("Banks must be created through the TruCalc Bank administration surface."))
        return super().create(vals_list)

    def write(self, vals):
        if {"trucalc_is_bank", "trucalc_bank_active"} & set(vals):
            raise AccessError(_("Bank status requires a controlled TruCalc action."))
        return super().write(vals)

    def unlink(self):
        if self.sudo().filtered("trucalc_is_bank"):
            raise AccessError(_("TruCalc Banks cannot be deleted."))
        return super().unlink()

    @api.constrains("trucalc_is_bank", "trucalc_bank_active")
    def _check_trucalc_bank_marker(self):
        main_company = self.env.ref("base.main_company")
        for company in self:
            if company == main_company and (company.trucalc_is_bank or company.trucalc_bank_active):
                raise ValidationError(_("The TruCalc main company cannot be a Bank."))
            if company.trucalc_bank_active and not company.trucalc_is_bank:
                raise ValidationError(_("Only a TruCalc Bank may have active Bank status."))
