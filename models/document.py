from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TrucalcDocument(models.Model):
    _name = "trucalc.document"
    _description = "TruCalc Document"
    _order = "upload_date desc"

    name = fields.Char(
        string="Document Name",
        required=True,
    )

    order_id = fields.Many2one(
        "trucalc.order",
        string="Evaluation Order",
        required=True,
        ondelete="cascade",
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="order_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )

    document_type = fields.Selection(
        [
            ("engagement", "Engagement Letter"),
            ("appraisal", "Appraisal Report"),
            ("review", "Review Report"),
            ("invoice", "Invoice"),
            ("environmental", "Environmental Report"),
            ("other", "Other"),
        ],
        string="Document Type",
        required=True,
        default="other",
    )

    attachment = fields.Binary(
        string="File",
        attachment=True,
    )

    filename = fields.Char(
        string="Filename",
    )

    upload_date = fields.Datetime(
        string="Upload Date",
        default=fields.Datetime.now,
        readonly=True,
    )

    uploaded_by = fields.Many2one(
        "res.users",
        string="Uploaded By",
        default=lambda self: self.env.user,
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if not user._trucalc_has_bank_role():
            return super().create(vals_list)

        bank_company = user._trucalc_bank_identity()
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            order_id = vals.get("order_id")
            order = self.env["trucalc.order"].sudo().browse(order_id).exists()
            if (
                not order_id
                or not order
                or order.company_id != bank_company
                or "company_id" in vals
                or "upload_date" in vals
                or (
                    "uploaded_by" in vals
                    and vals["uploaded_by"] != user.id
                )
            ):
                raise AccessError(_("TruCalc bank document ownership is not authorized."))
            vals["uploaded_by"] = user.id
            prepared.append(vals)

        trusted_context = dict(self.env.context)
        trusted_context["allowed_company_ids"] = []
        for field in ("company_id", "order_id", "uploaded_by", "upload_date"):
            trusted_context.pop("default_%s" % field, None)
        trusted_model = self.with_context(trusted_context)
        return super(TrucalcDocument, trusted_model).create(prepared)

    def write(self, vals):
        protected = {"order_id", "company_id", "uploaded_by", "upload_date"}
        if self.env.user._trucalc_has_external_role() and protected & vals.keys():
            raise AccessError(_("TruCalc document ownership and provenance are immutable."))
        return super().write(vals)
