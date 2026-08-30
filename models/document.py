import base64
import binascii
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
FILENAME_RE = re.compile(r"^[A-Za-z0-9 _().-]+$")
ALLOWED_DOCUMENT_EXTENSIONS = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx",
    "jpg", "jpeg", "png", "tif", "tiff",
})
ACCEPTED_OR_LATER = {
    "accepted", "bid_requested", "assigned", "engaged", "report_received",
    "reviewer_assigned", "under_review", "completed", "cancelled",
}


class TrucalcDocument(models.Model):
    _name = "trucalc.document"
    _description = "TruCalc Document"
    _order = "upload_date desc, id desc"

    name = fields.Char(string="Document Name", required=True, readonly=True)
    order_id = fields.Many2one(
        "trucalc.order", string="Evaluation Order", required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company", related="order_id.company_id", store=True,
        readonly=True, index=True,
    )
    tag_id = fields.Many2one(
        "trucalc.document.tag", string="Document Tag", required=True,
        ondelete="restrict", domain="[('active', '=', True)]",
    )
    origin = fields.Selection(
        [("bank", "Bank"), ("trucalc", "TruCalc")],
        required=True, readonly=True, index=True,
    )
    originating_bank_id = fields.Many2one(
        "res.company", readonly=True, index=True, ondelete="restrict"
    )
    attachment = fields.Binary(string="File", attachment=True)
    filename = fields.Char(string="Filename", required=True)
    upload_date = fields.Datetime(
        string="Upload Date", default=fields.Datetime.now, required=True, readonly=True,
    )
    uploaded_by = fields.Many2one(
        "res.users", string="Uploaded By", default=lambda self: self.env.user,
        required=True, readonly=True, ondelete="restrict",
    )
    visible_before_engagement = fields.Boolean(default=False)
    visible_after_engagement = fields.Boolean(default=False)
    active = fields.Boolean(default=True, required=True, readonly=True, index=True)
    deleted_at = fields.Datetime(readonly=True, index=True)
    deleted_by_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    event_ids = fields.One2many("trucalc.document.event", "document_id", readonly=True)

    _active_filename_unique = models.UniqueIndex(
        "(order_id, lower(filename)) WHERE active IS TRUE",
        "An active document with this filename already exists on this Order.",
    )

    @api.model
    def _validate_filename_value(self, filename):
        if not filename or filename != filename.strip() or not FILENAME_RE.fullmatch(filename):
            raise ValidationError(_(
                "Filename may contain only letters, numbers, spaces, hyphens, "
                "underscores, parentheses, and periods."
            ))
        basename, separator, extension = filename.rpartition(".")
        if not separator or not basename or not extension or basename in (".", ".."):
            raise ValidationError(_("Filename must contain a nonempty basename and extension."))
        # This is an extension policy only; it does not inspect MIME type or content.
        if extension.lower() not in ALLOWED_DOCUMENT_EXTENSIONS:
            raise ValidationError(_(
                "Unsupported document type. Permitted file types are: "
                "PDF, DOC, DOCX, XLS, XLSX, JPG, JPEG, PNG, TIF, and TIFF."
            ))
        return filename

    @api.model
    def _validate_file_value(self, attachment):
        if not attachment:
            raise ValidationError(_("A document file is required."))
        try:
            raw_size = len(base64.b64decode(attachment, validate=True))
        except (binascii.Error, ValueError, TypeError):
            raise ValidationError(_("The uploaded document data is invalid."))
        if raw_size > MAX_DOCUMENT_BYTES:
            raise ValidationError(_("A document may not exceed 50 MB."))
        return raw_size

    @api.model
    def _check_friendly_duplicate(self, order_id, filename):
        if self.sudo().with_context(active_test=False).search_count([
            ("order_id", "=", order_id), ("active", "=", True),
            ("filename", "=ilike", filename),
        ]):
            raise ValidationError(_(
                'An active document named "%(filename)s" already exists on this Order.',
                filename=filename,
            ))

    @api.model
    def _prepare_common_create(self, vals, order, actor, origin, bank=False):
        filename = self._validate_filename_value(vals.get("filename"))
        self._validate_file_value(vals.get("attachment"))
        tag = self.env["trucalc.document.tag"].sudo().browse(vals.get("tag_id")).exists()
        if not tag or not tag.active:
            raise ValidationError(_("Select an active Document Tag."))
        self._check_friendly_duplicate(order.id, filename)
        protected = {
            "company_id", "origin", "originating_bank_id", "uploaded_by",
            "upload_date", "active", "deleted_at", "deleted_by_id",
            "visible_before_engagement", "visible_after_engagement",
        }
        vals = {key: value for key, value in vals.items() if key not in protected}
        vals.update({
            "name": filename, "order_id": order.id, "origin": origin,
            "originating_bank_id": bank.id if bank else False,
            "uploaded_by": actor.id, "upload_date": fields.Datetime.now(),
            "visible_before_engagement": False, "visible_after_engagement": False,
            "active": True,
        })
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if user._trucalc_has_bank_role():
            raise AccessError(_("Bank documents require the controlled Bank upload workflow."))
        if not (
            user.has_group("trucalc_orders.group_trucalc_admin")
            or user.has_group("trucalc_orders.group_trucalc_operations")
        ):
            raise AccessError(_("You are not authorized to upload supporting documents."))
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            modal_order_value = self.env.context.get("trucalc_document_order_id")
            if modal_order_value:
                try:
                    modal_order_id = int(modal_order_value)
                    submitted_order_id = int(vals.get("order_id") or modal_order_id)
                except (TypeError, ValueError):
                    raise AccessError(_("The document Order is not authorized."))
                if submitted_order_id != modal_order_id:
                    raise AccessError(_("The document must remain on its originating Order."))
                vals["order_id"] = modal_order_id
            order = self.env["trucalc.order"].browse(vals.get("order_id")).exists()
            if not order:
                raise AccessError(_("The document Order is not authorized."))
            order.check_access("read")
            if order.status == "draft":
                raise AccessError(_(
                    "Bank Draft documents require the controlled Bank workflow."
                ))
            prepared.append(self._prepare_common_create(vals, order, user, "trucalc"))
        documents = super().create(prepared)
        for document in documents:
            self.env["trucalc.document.event"]._log_document_event(document, "uploaded", user)
        return documents

    @api.model
    @api.private
    def _create_bank_document(self, order, tag, filename, attachment, actor):
        order = order.sudo().exists()
        tag = tag.sudo().exists()
        actor = actor.sudo().exists()
        if len(order) != 1 or len(tag) != 1 or len(actor) != 1:
            raise AccessError(_("The Bank document upload is not authorized."))
        bank = actor._trucalc_bank_identity()
        uploader = (
            actor.has_group("trucalc_orders.group_bank_admin")
            or actor.has_group("trucalc_orders.group_bank_requestor")
        )
        if order.company_id != bank or not uploader:
            raise AccessError(_("The Bank document upload is not authorized."))
        if order.status == "draft":
            self.env["trucalc.order"].with_user(actor)._authorize_bank_draft(
                order, actor,
            )
        values = self._prepare_common_create({
            "order_id": order.id, "tag_id": tag.id,
            "filename": filename, "attachment": attachment,
        }, order, actor, "bank", bank)
        document = super(TrucalcDocument, self.sudo()).create(values)
        self.env["trucalc.document.event"]._log_document_event(
            document, "uploaded", actor,
            trucalc_notification_worthy=order.status in ACCEPTED_OR_LATER,
        )
        return document

    @api.model
    @api.private
    def _delete_bank_draft_document(self, document, order, actor):
        order, actor, bank = self.env["trucalc.order"].with_user(
            actor
        )._authorize_bank_draft(order, actor)
        document = document.sudo().exists()
        if (
            len(document) != 1
            or not document.active
            or document.order_id != order
            or document.origin != "bank"
            or document.originating_bank_id != bank
        ):
            raise AccessError(_("The Bank Draft document deletion is not authorized."))
        self.env["trucalc.document.event"]._log_document_event(
            document, "deleted", actor,
            prior_visible_before_engagement=document.visible_before_engagement,
            new_visible_before_engagement=False,
            prior_visible_after_engagement=document.visible_after_engagement,
            new_visible_after_engagement=False,
        )
        super(TrucalcDocument, document).write({
            "attachment": False,
            "active": False,
            "deleted_at": fields.Datetime.now(),
            "deleted_by_id": actor.id,
            "visible_before_engagement": False,
            "visible_after_engagement": False,
        })
        return True

    def _require_manager(self):
        if not (
            self.env.user.has_group("trucalc_orders.group_trucalc_admin")
            or self.env.user.has_group("trucalc_orders.group_trucalc_operations")
        ):
            raise AccessError(_("Only TruCalc Administrators and Operations may manage documents."))
        if self.filtered(lambda document: document.order_id.status == "draft"):
            raise AccessError(_("TruCalc personnel may not modify Bank Draft documents."))

    def _accepted_engagement_context(self):
        self.ensure_one()
        order = self.order_id
        engagement = order.current_engagement_id.sudo()
        authorization = engagement.assignment_authorization_id
        if (
            order.status == "engaged" and engagement and engagement.active
            and engagement.response_state == "accepted"
            and authorization.active and authorization.source == "assignment"
            and authorization.order_id == order
            and authorization.vendor_id == order.assigned_vendor_id
            and authorization.round_number == order.bidding_round
        ):
            return engagement, authorization
        return False, False

    def write(self, vals):
        self._require_manager()
        forbidden = {
            "name", "attachment", "filename", "order_id", "company_id", "origin",
            "originating_bank_id", "uploaded_by", "upload_date", "active",
            "deleted_at", "deleted_by_id", "event_ids",
        }
        if forbidden & vals.keys():
            raise AccessError(_("Document content, filename, ownership, and provenance are immutable."))
        allowed = {"tag_id", "visible_before_engagement", "visible_after_engagement"}
        if set(vals) - allowed:
            raise AccessError(_("The requested document change is not authorized."))
        for document in self:
            changes = {}
            if "tag_id" in vals and vals["tag_id"] != document.tag_id.id:
                tag = self.env["trucalc.document.tag"].sudo().browse(vals["tag_id"]).exists()
                if not tag or not tag.active:
                    raise ValidationError(_("Select an active Document Tag."))
                changes["tag_id"] = (document.tag_id, tag)
            for field_name in ("visible_before_engagement", "visible_after_engagement"):
                if field_name in vals and bool(vals[field_name]) != bool(document[field_name]):
                    changes[field_name] = (bool(document[field_name]), bool(vals[field_name]))
            if not changes:
                continue
            old_before = document.visible_before_engagement
            old_after = document.visible_after_engagement
            super(TrucalcDocument, document).write(vals)
            if "tag_id" in changes:
                old_tag, _new_tag = changes["tag_id"]
                self.env["trucalc.document.event"]._log_document_event(
                    document, "tag_changed", self.env.user, prior_tag_id=old_tag.id
                )
            if any(key in changes for key in ("visible_before_engagement", "visible_after_engagement")):
                engagement, authorization = document._accepted_engagement_context()
                newly_accessible = bool(
                    not old_after and document.visible_after_engagement
                    and engagement and authorization
                )
                self.env["trucalc.document.event"]._log_document_event(
                    document, "visibility_changed", self.env.user,
                    prior_visible_before_engagement=old_before,
                    new_visible_before_engagement=document.visible_before_engagement,
                    prior_visible_after_engagement=old_after,
                    new_visible_after_engagement=document.visible_after_engagement,
                    vendor_notification_worthy=newly_accessible,
                    vendor_id=engagement.vendor_id.id if newly_accessible else False,
                    authorization_id=authorization.id if newly_accessible else False,
                    engagement_id=engagement.id if newly_accessible else False,
                    bidding_round=document.order_id.bidding_round if newly_accessible else 0,
                )
        return True

    def action_controlled_delete(self):
        self._require_manager()
        for document in self:
            if not document.active:
                continue
            self.env["trucalc.document.event"]._log_document_event(
                document, "deleted", self.env.user,
                prior_visible_before_engagement=document.visible_before_engagement,
                new_visible_before_engagement=False,
                prior_visible_after_engagement=document.visible_after_engagement,
                new_visible_after_engagement=False,
            )
            super(TrucalcDocument, document).write({
                "attachment": False, "active": False,
                "deleted_at": fields.Datetime.now(),
                "deleted_by_id": self.env.user.id,
                "visible_before_engagement": False,
                "visible_after_engagement": False,
            })
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_download(self):
        self.ensure_one()
        self.check_access("read")
        if not self.active or not self.attachment:
            raise AccessError(_("This document is not available for download."))
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content?model={self._name}&id={self.id}&field=attachment"
                "&filename_field=filename&download=true"
            ),
            "target": "self",
        }

    def unlink(self):
        raise AccessError(_("Documents must be removed through controlled deletion."))

    @api.model
    @api.private
    def _vendor_authorized_documents(self, authorization, actor):
        vendor = actor.sudo().trucalc_vendor_id
        authorization = authorization.sudo().exists()
        if not vendor or not authorization or not authorization.active or authorization.vendor_id != vendor:
            return self.browse()
        order = authorization.order_id
        base = [("active", "=", True), ("order_id", "=", order.id)]
        if authorization.source == "invitation":
            invitation = authorization.invitation_id
            deadline_expired = bool(
                invitation.response_deadline
                and fields.Datetime.now() > invitation.response_deadline
            )
            if (
                order.status != "bid_requested"
                or authorization.round_number != order.bidding_round
                or not invitation or invitation.state != "invited" or deadline_expired
                or invitation.order_id != order or invitation.vendor_id != vendor
                or invitation.round_number != order.bidding_round
            ):
                return self.browse()
            return self.sudo().search(base + [("visible_before_engagement", "=", True)])
        if authorization.source == "assignment":
            engagement = self.env["trucalc.vendor.engagement"].sudo().search([
                ("assignment_authorization_id", "=", authorization.id), ("active", "=", True),
            ])
            if (
                len(engagement) != 1 or order.status != "engaged"
                or order.assigned_vendor_id != vendor
                or authorization.round_number != order.bidding_round
                or engagement.response_state != "accepted"
                or engagement.order_id != order or engagement.vendor_id != vendor
            ):
                return self.browse()
            return self.sudo().search(base + [("visible_after_engagement", "=", True)])
        return self.browse()
