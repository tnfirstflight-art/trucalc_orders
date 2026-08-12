from odoo import models, fields


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
