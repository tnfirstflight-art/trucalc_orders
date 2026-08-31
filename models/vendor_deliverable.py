import base64
import binascii

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .document import FILENAME_RE, MAX_DOCUMENT_BYTES


INVOICE_SUBMISSION_STATES = frozenset({
    "engaged", "report_received", "reviewer_assigned", "under_review",
})


class TruCalcVendorDeliverable(models.Model):
    _name = "trucalc.vendor.deliverable"
    _description = "Immutable Vendor Deliverable"
    _order = "order_id, artifact_type, version desc, submitted_at desc, id desc"
    _rec_name = "filename"

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", related="order_id.company_id", store=True, readonly=True,
        index=True,
    )
    artifact_type = fields.Selection(
        [("valuation", "Valuation"), ("vendor_invoice", "Vendor Invoice")],
        required=True, readonly=True, index=True,
    )
    filename = fields.Char(required=True, readonly=True)
    file_data = fields.Binary(string="File", attachment=True, required=True, readonly=True)
    submitted_at = fields.Datetime(required=True, readonly=True, index=True)
    submitted_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict",
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", required=True, readonly=True, index=True, ondelete="restrict",
    )
    engagement_id = fields.Many2one(
        "trucalc.vendor.engagement", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    authorization_id = fields.Many2one(
        "trucalc.order.vendor.authorization", required=True, readonly=True,
        index=True, ondelete="restrict",
    )
    bidding_round = fields.Integer(required=True, readonly=True)
    version = fields.Integer(required=True, readonly=True, default=0)
    is_current = fields.Boolean(required=True, readonly=True, default=False, index=True)
    status = fields.Selection(
        [("submitted", "Submitted")], required=True, readonly=True,
        default="submitted", index=True,
    )
    filename_link = fields.Html(
        compute="_compute_filename_link", sanitize=False, readonly=True,
        string="Filename",
    )

    _valuation_version_unique = models.UniqueIndex(
        "(order_id, version) WHERE artifact_type = 'valuation'",
        "A Valuation version may exist only once per Order.",
    )
    _current_valuation_unique = models.UniqueIndex(
        "(order_id) WHERE artifact_type = 'valuation' AND is_current IS TRUE",
        "An Order may have only one current Valuation.",
    )
    _vendor_invoice_unique = models.UniqueIndex(
        "(order_id) WHERE artifact_type = 'vendor_invoice'",
        "An Order may have only one Vendor Invoice.",
    )
    _artifact_structure = models.Constraint(
        "CHECK((artifact_type = 'valuation' AND version > 0) OR "
        "(artifact_type = 'vendor_invoice' AND version = 0 AND NOT is_current))",
        "Deliverable version/current metadata does not match its artifact type.",
    )
    _bidding_round_positive = models.Constraint(
        "CHECK(bidding_round > 0)", "The deliverable bidding round must be positive.",
    )

    @api.depends("filename")
    def _compute_filename_link(self):
        for deliverable in self:
            deliverable.filename_link = (
                Markup('<a href="/trucalc/deliverables/%s/download">%s</a>')
                % (deliverable.id, escape(deliverable.filename))
                if deliverable.id and deliverable.filename else False
            )

    @api.model
    def _validate_pdf(self, filename, file_data):
        if not filename or filename != filename.strip() or not FILENAME_RE.fullmatch(filename):
            raise ValidationError(_(
                "Filename may contain only letters, numbers, spaces, hyphens, "
                "underscores, parentheses, and periods."
            ))
        basename, separator, extension = filename.rpartition(".")
        if not separator or not basename or extension.lower() != "pdf" or basename in (".", ".."):
            raise ValidationError(_("Vendor deliverables must use a valid PDF filename."))
        if not file_data:
            raise ValidationError(_("A PDF file is required."))
        try:
            payload = base64.b64decode(file_data, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ValidationError(_("The uploaded PDF data is invalid."))
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise ValidationError(_("A Vendor deliverable may not exceed 50 MB."))
        if not payload.startswith(b"%PDF-"):
            raise ValidationError(_("The uploaded file does not contain a valid PDF signature."))
        return filename

    @api.model
    def _vendor_identity(self, actor):
        actor = actor.sudo().exists()
        if (
            len(actor) != 1 or actor != self.env.user or not actor.active
            or not actor.has_group("trucalc_orders.group_vendor_portal")
            or not actor.has_group("base.group_portal")
        ):
            raise AccessError(_("Vendor deliverable access is not authorized."))
        vendor = actor.trucalc_vendor_id
        if not vendor or not vendor.active:
            raise AccessError(_("Vendor deliverable access is not authorized."))
        return actor, vendor

    @api.model
    def _prevalidate_authorization(self, authorization, actor):
        actor, vendor = self._vendor_identity(actor)
        authorization = authorization.sudo().exists()
        if (
            len(authorization) != 1 or not authorization.active
            or authorization.source != "assignment"
            or authorization.vendor_id != vendor
        ):
            raise AccessError(_("Vendor deliverable access is not authorized."))
        return authorization, actor, vendor

    @api.model
    def _lock_and_revalidate(self, authorization, actor, artifact_type):
        authorization, actor, vendor = self._prevalidate_authorization(
            authorization, actor,
        )
        order = authorization.order_id
        order._lock_for_bid_lifecycle()
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_engagement "
            "WHERE assignment_authorization_id = %s AND active IS TRUE FOR UPDATE",
            (authorization.id,),
        )
        engagement_ids = [row[0] for row in self.env.cr.fetchall()]
        authorization.invalidate_recordset()
        order.invalidate_recordset()
        authorization, actor, vendor = self._prevalidate_authorization(
            authorization, actor,
        )
        engagement = self.env["trucalc.vendor.engagement"].sudo().browse(
            engagement_ids
        ).exists()
        eligible_states = (
            {"engaged"} if artifact_type == "valuation" else INVOICE_SUBMISSION_STATES
        )
        if (
            len(engagement) != 1 or not engagement.active
            or engagement.response_state != "accepted"
            or engagement.assignment_authorization_id != authorization
            or engagement.order_id != order
            or engagement.vendor_id != vendor
            or engagement.company_id != order.company_id
            or engagement.round_number != authorization.round_number
            or authorization.order_id != order
            or authorization.company_id != order.company_id
            or authorization.round_number != order.bidding_round
            or order.assigned_vendor_id != vendor
            or order.status not in eligible_states
        ):
            raise AccessError(_("Vendor deliverable access is not authorized."))
        return order, engagement, authorization, actor, vendor

    @api.model
    @api.private
    def _submit(self, authorization, actor, artifact_type, filename, file_data):
        if artifact_type not in ("valuation", "vendor_invoice"):
            raise AccessError(_("Vendor deliverable type is not authorized."))
        filename = self._validate_pdf(filename, file_data)
        order, engagement, authorization, actor, vendor = self._lock_and_revalidate(
            authorization, actor, artifact_type,
        )
        existing = self.sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", artifact_type),
        ], limit=1)
        if existing:
            raise ValidationError(_(
                "This Vendor deliverable has already been submitted and is immutable."
            ))
        values = {
            "order_id": order.id,
            "artifact_type": artifact_type,
            "filename": filename,
            "file_data": file_data,
            "submitted_at": fields.Datetime.now(),
            "submitted_by_id": actor.id,
            "vendor_id": vendor.id,
            "engagement_id": engagement.id,
            "authorization_id": authorization.id,
            "bidding_round": order.bidding_round,
            "version": 1 if artifact_type == "valuation" else 0,
            "is_current": artifact_type == "valuation",
            "status": "submitted",
        }
        deliverable = super(TruCalcVendorDeliverable, self.sudo()).create(values)
        if artifact_type == "valuation":
            order._transition_status(
                "engaged", "report_received", "valuation_received",
                deliverable=deliverable, actor=actor,
            )
        return deliverable

    @api.model
    @api.private
    def _vendor_deliverables(self, authorization, actor):
        try:
            authorization, actor, vendor = self._prevalidate_authorization(
                authorization, actor,
            )
        except AccessError:
            return self.browse()
        order = authorization.order_id
        engagement = self.env["trucalc.vendor.engagement"].sudo().search([
            ("assignment_authorization_id", "=", authorization.id),
            ("active", "=", True),
        ])
        if (
            len(engagement) != 1 or engagement.response_state != "accepted"
            or engagement.vendor_id != vendor or engagement.order_id != order
            or engagement.company_id != order.company_id
            or engagement.round_number != authorization.round_number
            or authorization.round_number != order.bidding_round
            or order.assigned_vendor_id != vendor
            or order.status not in INVOICE_SUBMISSION_STATES
        ):
            return self.browse()
        return self.sudo().search([
            ("order_id", "=", order.id), ("vendor_id", "=", vendor.id),
            "|", ("artifact_type", "=", "vendor_invoice"),
            "&", ("artifact_type", "=", "valuation"), ("is_current", "=", True),
        ])

    def _authorize_download(self, actor):
        self.ensure_one()
        deliverable = self.sudo().exists()
        if len(deliverable) != 1:
            raise AccessError(_("Vendor deliverable download is not authorized."))
        actor = actor.sudo().exists()
        if len(actor) != 1 or actor != self.env.user or not actor.active:
            raise AccessError(_("Vendor deliverable download is not authorized."))
        if actor.has_group("trucalc_orders.group_vendor_portal"):
            allowed = self._vendor_deliverables(deliverable.authorization_id, actor)
            if deliverable not in allowed:
                raise AccessError(_("Vendor deliverable download is not authorized."))
            return deliverable
        if actor.has_group("trucalc_orders.group_trucalc_admin") or actor.has_group(
            "trucalc_orders.group_trucalc_operations"
        ):
            deliverable.with_user(actor).check_access("read")
            return deliverable
        raise AccessError(_("Vendor deliverable download is not authorized."))

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor deliverables require a trusted submission workflow."))

    def write(self, vals):
        raise AccessError(_("Vendor deliverables are immutable."))

    def unlink(self):
        raise AccessError(_("Vendor deliverables are immutable."))
