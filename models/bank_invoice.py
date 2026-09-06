import base64
import math
from datetime import timedelta
from io import BytesIO

from psycopg2.errors import UniqueViolation

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.misc import format_amount
from odoo.tools.pdf import PdfReader


INTERNAL = "trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations"


class BankInvoice(models.Model):
    _name = "trucalc.bank.invoice"
    _description = "TruCalc Bank Invoice"
    _rec_name = "invoice_number"
    _order = "issued_at desc, id desc"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, ondelete="restrict", index=True)
    invoice_number = fields.Char(required=True, readonly=True, index=True)
    borrower = fields.Char(related="order_id.borrower", readonly=True, compute_sudo=False, groups=INTERNAL)
    property_address = fields.Char(related="order_id.property_address", readonly=True, compute_sudo=False, groups=INTERNAL)
    invoice_date = fields.Date(required=True, readonly=True)
    due_date = fields.Date(required=True, readonly=True)
    amount = fields.Monetary(required=True, readonly=True)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True, ondelete="restrict")
    status = fields.Selection([("issued", "Issued"), ("paid", "Paid"), ("void", "Void")], required=True, readonly=True)
    issued_by = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    issued_at = fields.Datetime(required=True, readonly=True)
    current_fee_change_request_id = fields.Many2one("trucalc.fee.change.request", readonly=True, ondelete="restrict")
    fee_workflow_revision = fields.Integer(required=True, readonly=True)
    original_agreed_fee = fields.Monetary(required=True, readonly=True)
    issuer_company_id = fields.Many2one("res.company", required=True, readonly=True, ondelete="restrict")
    printed_values = fields.Json(required=True, readonly=True, groups=INTERNAL)
    pdf_data = fields.Binary(required=True, readonly=True, attachment=False, groups=INTERNAL)
    pdf_filename = fields.Char(required=True, readonly=True)
    paid_date = fields.Date(readonly=True, help="Payment date; future Date Paid import uses this field.")
    paid_at = fields.Datetime(readonly=True, help="Time the payment acknowledgment was recorded.")
    paid_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    payment_reference = fields.Char(string="Check # / Payment Reference", readonly=True)
    voided_at = fields.Datetime(readonly=True)
    voided_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    void_reason = fields.Text(readonly=True)

    _one_order = models.Constraint("UNIQUE(order_id)", "This Order already has a Bank Invoice, including any voided invoice.")
    _unique_number = models.Constraint("UNIQUE(invoice_number)", "The invoice number is already used.")
    _net_thirty = models.Constraint("CHECK(due_date = invoice_date + 30)", "Invoices require Net 30 terms.")
    _status_evidence = models.Constraint("""CHECK(
        (status = 'issued' AND paid_at IS NULL AND paid_by IS NULL AND paid_date IS NULL
            AND payment_reference IS NULL AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)
        OR (status = 'paid' AND paid_at IS NOT NULL AND paid_by IS NOT NULL AND paid_date IS NOT NULL
            AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)
        OR (status = 'void' AND paid_at IS NULL AND paid_by IS NULL AND paid_date IS NULL
            AND payment_reference IS NULL AND voided_at IS NOT NULL AND voided_by IS NOT NULL
            AND length(trim(void_reason)) > 0))""", "Invoice status evidence is inconsistent.")

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Bank Invoices require the controlled issue action."))

    def write(self, vals):
        raise AccessError(_("Bank Invoices are immutable outside controlled status actions."))

    def unlink(self):
        raise AccessError(_("Bank Invoices cannot be deleted."))

    def copy(self, default=None):
        raise AccessError(_("Bank Invoices cannot be copied or replaced."))

    @api.private
    def _can_return_content(self, field_name=None, access_token=None):
        raise AccessError(_("Use the authorized Bank Invoice download."))

    @api.model
    @api.private
    def _authorize_order(self, order):
        order.ensure_one()
        actor = self.env.user
        order = order.with_user(actor)
        order.check_access("read")
        if (not actor.active or actor.share or actor._trucalc_has_external_role()
                or not (actor.has_group("trucalc_orders.group_trucalc_admin")
                        or actor.has_group("trucalc_orders.group_trucalc_operations"))
                or not order.exists() or not order.sudo().company_id
                or order.sudo().company_id not in actor.company_ids):
            raise AccessError(_("Bank Invoices require authorized TruCalc Administrator or Operations access."))
        return actor

    @api.private
    def _authorize(self):
        self.ensure_one()
        self.with_user(self.env.user).check_access("read")
        invoice = self.sudo().exists()
        if not invoice or invoice.company_id != invoice.order_id.company_id:
            raise AccessError(_("This Bank Invoice is not authorized."))
        return self._authorize_order(invoice.order_id)

    @api.model
    @api.private
    def _printed_snapshot(self, order, fee, issued_at):
        # The installation's TruCalc company, never the user's selected customer Bank.
        issuer = self.env.ref("base.main_company").sudo()
        if not issuer.name or issuer == order.company_id:
            raise ValidationError(_("A distinct configured TruCalc issuer company is required."))
        invoice_date = issued_at.date()
        address = [issuer.street, issuer.street2,
                   " ".join(filter(None, [issuer.city, issuer.state_id.code, issuer.zip])),
                   issuer.country_id.name]
        logo = issuer.logo
        if isinstance(logo, bytes):
            logo = logo.decode("ascii")
        return issuer, {
            "issuer_name": issuer.name, "issuer_address": [line for line in address if line],
            "issuer_phone": issuer.phone or "", "issuer_email": issuer.email or "",
            "issuer_logo": logo or "", "bank_name": order.company_id.name,
            "invoice_number": order.order_number, "invoice_date": str(invoice_date),
            "due_date": str(invoice_date + timedelta(days=30)),
            "borrower": order.borrower or "", "loan_number": order.loan_number or "",
            "property_address": ", ".join(filter(None, [order.property_address, order.city, order.state, order.zip_code])),
            "service_type": dict(order._fields["service_type"]._description_selection(self.env)).get(order.service_type, ""),
            "amount_display": format_amount(self.env, fee["amount"], order.fee_currency_id),
            "currency": order.fee_currency_id.name,
        }

    @api.model
    @api.private
    def _render_issued_pdf(self, snapshot):
        html = self.env["ir.qweb"]._render("trucalc_orders.bank_invoice_pdf", {"invoice": snapshot})
        report = self.env["ir.actions.report"]
        if report.get_wkhtmltopdf_state() != "ok":
            raise ValidationError(_("A working PDF renderer is required to issue a Bank Invoice."))
        pdf = report._run_wkhtmltopdf([str(html)], report_ref="trucalc_orders.action_bank_invoice_report")
        try:
            if not pdf or not pdf.startswith(b"%PDF-"):
                raise ValueError()
            reader = PdfReader(BytesIO(pdf))
            if len(reader.pages) != 1 or not reader.pages[0].extract_text().strip():
                raise ValueError()
        except Exception as error:
            raise ValidationError(_("Invoice PDF generation did not produce a readable single-page document.")) from error
        return pdf

    @api.model
    @api.private
    def _issue_for_completion(self, order, issued_at):
        actor = self._authorize_order(order)
        try:
            with self.env.cr.savepoint():
                order = order.sudo()
                order._lock_for_bid_lifecycle()
                order.invalidate_recordset()
                self._authorize_order(order)
                if order.status != "under_review":
                    raise ValidationError(_("Bank Invoices are created only by Order completion."))
                if self.sudo().search_count([("order_id", "=", order.id)]):
                    raise ValidationError(_("This Order already has a Bank Invoice. Replacement invoices are not allowed."))
                fee = order._get_current_effective_fee()
                if fee["has_pending_request"]:
                    raise ValidationError(_("A pending fee change prevents invoice issue."))
                if (fee["company_id"] != order.company_id.id or fee["order_id"] != order.id
                        or fee["currency_id"] != order.fee_currency_id.id or not order.fee_currency_id
                        or not math.isfinite(fee["amount"]) or fee["amount"] < 0):
                    raise ValidationError(_("The invoice fee, Bank or currency is inconsistent."))
                if not order.order_number or order.order_number == "New":
                    raise ValidationError(_("A final Order number is required for invoicing."))
                now = issued_at
                issuer, snapshot = self._printed_snapshot(order, fee, now)
                pdf = self._render_issued_pdf(snapshot)
                invoice = super(BankInvoice, self.sudo()).create({
                    "order_id": order.id, "company_id": fee["company_id"],
                    "invoice_number": order.order_number, "invoice_date": now.date(),
                    "due_date": now.date() + timedelta(days=30), "amount": fee["amount"],
                    "currency_id": fee["currency_id"], "status": "issued", "issued_by": actor.id,
                    "issued_at": now, "current_fee_change_request_id": fee["current_fee_change_request_id"],
                    "fee_workflow_revision": fee["fee_workflow_revision"],
                    "original_agreed_fee": fee["original_agreed_fee"], "issuer_company_id": issuer.id,
                    "printed_values": snapshot, "pdf_data": base64.b64encode(pdf),
                    "pdf_filename": order.order_number + ".pdf",
                })
                self.env["trucalc.order.lifecycle.event"]._log_bank_invoice(invoice, "bank_invoice_issued")
                return invoice.with_user(actor)
        except UniqueViolation as error:
            raise ValidationError(_("This Order or invoice number has already been invoiced.")) from error

    @api.private
    def _lock_transition(self):
        actor = self._authorize()
        invoice = self.sudo()
        invoice.order_id._lock_for_bid_lifecycle()
        invoice.flush_recordset()
        self.env.cr.execute("SELECT id FROM trucalc_bank_invoice WHERE id = %s FOR UPDATE", (self.id,))
        invoice.invalidate_recordset()
        invoice.order_id.invalidate_recordset()
        self._authorize()
        if invoice.status != "issued" or invoice.order_id.status != "completed":
            raise ValidationError(_("Only an Issued invoice on a Completed Order may be marked Paid or Void."))
        return invoice, actor

    @api.private
    def _mark_paid(self, payment_date, payment_reference=None):
        """Manual and future File #/Date Paid/Check # imports share this transition.

        The actor is always the authenticated environment user, never an imported ID.
        paid_date is the payment's business date; paid_at is the actual audit time.
        """
        self._authorize()
        try:
            payment_date = fields.Date.to_date(payment_date)
        except (TypeError, ValueError):
            raise ValidationError(_("A valid Date Paid is required.")) from None
        if not payment_date or payment_date > fields.Date.today():
            raise ValidationError(_("Date Paid is required and cannot be in the future."))
        reference = payment_reference.strip() if isinstance(payment_reference, str) else ""
        if len(reference) > 255 or (payment_reference and not isinstance(payment_reference, str)):
            raise ValidationError(_("Payment reference must be at most 255 characters."))
        with self.env.cr.savepoint():
            invoice, actor = self._lock_transition()
            if payment_date < invoice.invoice_date:
                raise ValidationError(_("Date Paid cannot precede the Invoice Date."))
            super(BankInvoice, invoice).write({"status": "paid", "paid_date": payment_date,
                "paid_at": fields.Datetime.now(), "paid_by": actor.id, "payment_reference": reference or False})
            self.env["trucalc.order.lifecycle.event"]._log_bank_invoice(invoice, "bank_invoice_marked_paid")
        return True

    @api.private
    def _void(self, reason):
        self._authorize()
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason or len(reason) > 5000:
            raise ValidationError(_("A Void reason of 1 to 5000 characters is required."))
        with self.env.cr.savepoint():
            invoice, actor = self._lock_transition()
            super(BankInvoice, invoice).write({"status": "void", "voided_at": fields.Datetime.now(),
                "voided_by": actor.id, "void_reason": reason})
            self.env["trucalc.order.lifecycle.event"]._log_bank_invoice(invoice, "bank_invoice_voided")
        return True

    def action_mark_paid(self):
        return self._open_transition("paid")

    def action_void(self):
        return self._open_transition("void")

    @api.private
    def _open_transition(self, transition):
        self._authorize()
        if self.status != "issued":
            raise ValidationError(_("This invoice is already Paid or Void."))
        return {"type": "ir.actions.act_window", "name": _("Mark Paid") if transition == "paid" else _("Void Invoice"),
                "res_model": "trucalc.bank.invoice.status.wizard", "view_mode": "form", "target": "new",
                "context": {"default_invoice_id": self.id, "default_transition": transition}}

    def action_download(self):
        self._authorize()
        return {"type": "ir.actions.act_url", "url": "/trucalc/bank-invoices/%s/download" % self.id, "target": "self"}

    @api.model
    @api.private
    def _bank_invoice(self, order, invoice_id=None):
        actor = self.env.user
        bank = actor._trucalc_bank_identity()
        if not actor.active:
            raise AccessError(_("Invoice access is not authorized."))
        order.with_user(actor).check_access("read")
        if order.sudo().company_id != bank or order.sudo().status != "completed":
            raise AccessError(_("Invoice access is not authorized."))
        domain = [("order_id", "=", order.id), ("company_id", "=", bank.id)]
        if invoice_id is not None:
            domain.append(("id", "=", invoice_id))
        return self.sudo().search(domain, limit=1)

    @api.model
    @api.private
    def _portal_values(self, order):
        if order.sudo().status != "completed":
            return False
        invoice = self._bank_invoice(order)
        if not invoice:
            return False
        return {"id": invoice.id, "number": invoice.invoice_number,
                "invoice_date": str(invoice.invoice_date), "due_date": str(invoice.due_date),
                "amount": invoice.printed_values["amount_display"], "currency": invoice.printed_values["currency"],
                "status": dict(self._fields["status"]._description_selection(self.env))[invoice.status]}


class BankInvoiceReport(models.AbstractModel):
    _name = "report.trucalc_orders.bank_invoice_pdf"
    _description = "Bank Invoice Private Issue Renderer"

    @api.model
    def _get_report_values(self, docids, data=None):
        # All downloads use stored bytes. Generic report routes cannot regenerate.
        raise AccessError(_("Use the authorized Bank Invoice download; invoices cannot be regenerated."))
