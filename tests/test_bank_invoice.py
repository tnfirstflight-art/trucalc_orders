import base64
from ast import literal_eval
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.pdf import PdfReader

from .test_review_workflow import PDF


class BankInvoiceFixtures:
    password = "4E2-disposable-test-password"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank = cls.env["res.company"].create({"name": "4E2 Invoice Bank"})
        cls.other_bank = cls.env["res.company"].create({"name": "4E2 Other Bank"})
        cls.admin = cls._invoice_user("admin", ["group_trucalc_admin"])
        cls.ops = cls._invoice_user("ops", ["group_trucalc_operations"])
        cls.reviewer = cls._invoice_user("reviewer", ["group_trucalc_operations", "group_trucalc_reviewer"], scope=False)
        cls.bank_admin = cls._invoice_user("bank-admin", ["group_bank_admin"], bank=cls.bank)
        cls.bank_requestor = cls._invoice_user("bank-requestor", ["group_bank_requestor"], bank=cls.bank)
        cls.bank_view = cls._invoice_user("bank-view", ["group_bank_view_only"], bank=cls.bank)
        cls.foreign = cls._invoice_user("foreign", ["group_bank_admin"], bank=cls.other_bank)
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4E2 Invoice Vendor"})
        cls.env["trucalc.vendor.fee"].create({"vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 321})
        cls.vendor_user = cls._invoice_user("vendor", ["group_vendor_portal"], vendor=cls.vendor)
        cls.area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": cls.env.ref("base.state_us_37").id,
            "county": "4E2 Invoice County", "service_type": "evaluation", "base_fee": 500,
        })

    @classmethod
    def _invoice_user(cls, suffix, groups, bank=False, vendor=False, scope=True):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4E2 " + suffix, "login": "4e2-" + suffix, "password": cls.password,
            "company_id": cls.env.company.id,
            "company_ids": [Command.set((cls.env.company | cls.bank).ids if scope else cls.env.company.ids)],
            "group_ids": [Command.set([cls.env.ref("trucalc_orders." + name).id for name in groups])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _priced_order(self):
        values = {"borrower": "Invoice Borrower", "property_address": "42 Invoice Street",
            "city": "Invoice City", "zip_code": "38600", "loan_number": "LOAN-4E2",
            "service_type": "evaluation", "property_type": "commercial",
            "due_date": str(fields.Date.today() + timedelta(days=14)),
            "inspection_contact_name": "Invoice Contact", "inspection_contact_phone": "9015550100",
            "inspection_contact_email": "invoice@example.test", "notes": "PRIVATE INTERNAL NOTE",
            "service_area_id": str(self.area.id), "service_state_id": str(self.area.state_id.id),
            "service_county": self.area.county}
        model = self.env["trucalc.order"].with_user(self.bank_admin)
        order = model._create_bank_draft(values, self.bank_admin)
        model._send_bank_draft(order, values, self.bank_admin)
        order.with_user(self.admin).action_accept_request()
        return order

    def _ready_invoice_order(self, approve_fee=False):
        order = self._priced_order()
        if approve_fee:
            rid = order.with_user(self.admin).action_request_fee_change(650, "PRIVATE FEE REASON")
            self.env["trucalc.fee.change.request"].browse(rid).with_user(self.bank_admin)._decide(order, "approved")
        order.with_user(self.admin).action_request_vendor_bids(self.vendor, fields.Datetime.now() + timedelta(days=2))
        invitation = order.invitation_ids.filtered(lambda item: item.vendor_id == self.vendor)
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response("standard_terms_accepted")
        bid.with_user(self.ops)._action_confirm_engagement()
        authorization = order.vendor_authorization_ids.filtered(lambda item: item.active and item.source == "assignment")
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        self.env["trucalc.vendor.order"].with_user(self.vendor_user).browse(authorization.id).action_vendor_accept_engagement()
        deliverables = self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)
        valuation = deliverables._submit(authorization, self.vendor_user, "valuation", "Valuation.pdf", PDF)
        deliverables._submit(authorization, self.vendor_user, "vendor_invoice", "Vendor.pdf", PDF)
        order.with_user(self.admin).action_assign_reviewer(self.reviewer)
        order.with_user(self.reviewer).action_start_review()
        order.with_user(self.reviewer).action_approve_valuation(valuation)
        return order

    def _issue_invoice(self, order=None, actor=None):
        order = order or self._ready_invoice_order()
        order.with_user(actor or self.admin).action_complete_order()
        return self.env["trucalc.bank.invoice"].with_user(actor or self.admin).search([("order_id", "=", order.id)])


@tagged("post_install", "-at_install", "trucalc_bank_invoice")
class TestBankInvoice(BankInvoiceFixtures, TransactionCase):
    def test_unpaid_queue_domain_columns_and_scope(self):
        action = self.env.ref("trucalc_orders.action_unpaid_bank_invoices")
        domain = literal_eval(action.domain)
        self.assertEqual(action.res_model, "trucalc.bank.invoice")
        self.assertEqual(domain, [("status", "=", "issued")])
        menu = self.env.ref("trucalc_orders.menu_unpaid_bank_invoices")
        roles = self.env.ref("trucalc_orders.group_trucalc_admin") | self.env.ref("trucalc_orders.group_trucalc_operations")
        self.assertEqual(menu.group_ids, roles)
        self.assertEqual(action.group_ids, roles)
        self.assertEqual(menu.action, action)
        arch = etree.fromstring(action.view_id.arch_db)
        self.assertEqual(arch.tag, "list")
        for flag in ("create", "edit", "delete", "duplicate"):
            self.assertEqual(arch.get(flag), "0")
        self.assertEqual(arch.xpath("./field/@name"), ["invoice_number", "company_id", "borrower",
            "property_address", "invoice_date", "due_date", "currency_id", "amount", "status"])
        button = arch.xpath("./button[@name='action_mark_paid']")
        self.assertEqual(len(button), 1)
        self.assertEqual(button[0].get("string"), "Pay")
        self.assertEqual(button[0].get("type"), "object")
        self.assertEqual(button[0].get("invisible"), "status != 'issued'")
        issued, paid, void = [self._issue_invoice() for _ in range(3)]
        paid._mark_paid(fields.Date.today())
        void._void("Queue fixture")
        queue = self.env[action.res_model].with_user(self.ops).search(domain)
        self.assertIn(issued, queue)
        self.assertNotIn(paid, queue)
        self.assertNotIn(void, queue)
        self.assertEqual(len(queue.ids), len(set(queue.ids)))
        self.assertEqual((issued.borrower, issued.property_address),
                         (issued.order_id.borrower, issued.order_id.property_address))
        for name in ("borrower", "property_address"):
            self.assertFalse(issued._fields[name].store)
            self.assertFalse(issued._fields[name].compute_sudo)
        outsider = self._invoice_user("queue-other-company", ["group_trucalc_operations"], scope=False)
        self.assertNotIn(issued, self.env[action.res_model].with_user(outsider).search(domain))
        with self.assertRaises(AccessError):
            issued.with_user(outsider).action_mark_paid()

    def test_unpaid_row_pay_reuses_wizard_and_shared_transition(self):
        invoice = self._issue_invoice()
        remaining = self._issue_invoice()
        # Reviewer is an additive capability, not a standalone internal persona.
        with self.assertRaisesRegex(ValidationError, "requires exactly one normal"), self.env.cr.savepoint():
            self._invoice_user("queue-reviewer-only", ["group_trucalc_reviewer"])
        # This assigned reviewer has review access but no Bank company scope.
        for actor in (self.reviewer, self.bank_admin, self.bank_requestor, self.bank_view, self.vendor_user):
            with self.assertRaises(AccessError):
                invoice.with_user(actor).action_mark_paid()
            self.assertFalse(invoice.with_user(actor).has_access("read"))
        for actor in (self.admin, self.ops):
            action = invoice.with_user(actor).action_mark_paid()
            self.assertEqual(action["res_model"], "trucalc.bank.invoice.status.wizard")
            self.assertEqual(action["target"], "new")
            self.assertEqual(action["context"], {"default_invoice_id": invoice.id, "default_transition": "paid"})
        wizard = self.env[action["res_model"]].with_user(self.ops).with_context(**action["context"]).create({
            "payment_date": fields.Date.today(), "payment_reference": "  QUEUE-CHECK-001  "})
        original = type(invoice)._mark_paid
        with patch.object(type(invoice), "_mark_paid", autospec=True, side_effect=original) as transition:
            self.assertEqual(wizard.action_confirm(), {"type": "ir.actions.act_window_close"})
            transition.assert_called_once()
        self.assertEqual((invoice.status, invoice.paid_date, invoice.payment_reference, invoice.paid_by),
                         ("paid", fields.Date.today(), "QUEUE-CHECK-001", self.ops))
        self.assertTrue(invoice.paid_at)
        self.assertEqual(invoice.invoice_number, invoice.order_id.order_number)
        queue = self.env[invoice._name].with_user(self.ops).search(
            literal_eval(self.env.ref("trucalc_orders.action_unpaid_bank_invoices").domain))
        self.assertNotIn(invoice, queue)
        self.assertIn(remaining, queue)
        self.assertEqual(self.env["trucalc.order.lifecycle.event"].search_count([
            ("bank_invoice_id", "=", invoice.id), ("event_type", "=", "bank_invoice_marked_paid")]), 1)
        with self.assertRaises(ValidationError):
            invoice.action_mark_paid()
        with self.assertRaises(ValidationError):
            wizard.action_confirm()
        with self.assertRaises(ValidationError):
            invoice.action_void()

    def test_real_pdf_snapshot_net_thirty_and_no_order_write(self):
        order = self._ready_invoice_order(approve_fee=True)
        self.env.flush_all()
        self.env.cr.execute("SELECT to_jsonb(o) FROM trucalc_order o WHERE id=%s", (order.id,))
        before = self.env.cr.fetchone()[0]
        original = type(order)._get_current_effective_fee
        with patch.object(type(order), "_get_current_effective_fee", autospec=True, side_effect=original) as helper:
            invoice = self._issue_invoice(order, self.ops)
            helper.assert_called_once()
        self.assertEqual(invoice.amount, 650)
        self.assertEqual(invoice.original_agreed_fee, 500)
        self.assertEqual(invoice.invoice_number, order.order_number)
        self.assertEqual(invoice.current_fee_change_request_id, order.current_fee_change_request_id)
        self.assertEqual(invoice.fee_workflow_revision, order.fee_workflow_revision)
        self.assertEqual(invoice.due_date, invoice.invoice_date + timedelta(days=30))
        self.assertEqual(invoice.issued_by, self.ops)
        self.env.flush_all()
        self.env.cr.execute("SELECT to_jsonb(o) FROM trucalc_order o WHERE id=%s", (order.id,))
        after = self.env.cr.fetchone()[0]
        self.assertEqual(after["status"], "completed")
        for key in ("status", "write_date", "write_uid"):
            before.pop(key, None); after.pop(key, None)
        self.assertEqual(after, before)
        pdf = base64.b64decode(invoice.pdf_data)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        reader = PdfReader(BytesIO(pdf))
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text().replace("\t", " ")
        for value in (order.order_number, "650", "Invoice Borrower", "LOAN-4E2", "Remit Payment To", self.bank.name):
            self.assertIn(value, text)
        for value in ("PRIVATE FEE REASON", "PRIVATE INTERNAL NOTE", "Vendor", "Reviewer", "Negotiated", "Base Fee"):
            self.assertNotIn(value, text)
        snapshot = dict(invoice.printed_values)
        self.env.ref("base.main_company").write({"street": "Changed after issue", "name": "Changed issuer"})
        self.bank.write({"name": "Changed Bank"})
        self.assertEqual(invoice.pdf_data, base64.b64encode(pdf))
        self.assertEqual(invoice.printed_values, snapshot)
        self.assertFalse(self.env["ir.attachment"].sudo().search_count([("res_model", "=", invoice._name), ("res_id", "=", invoice.id)]))
        with self.assertRaises(ValidationError):
            self._issue_invoice(order)

    def test_eligibility_fail_closed_zero_and_forged_contract(self):
        order = self._priced_order()
        with self.assertRaises(ValidationError):
            self._issue_invoice(order)
        order = self._ready_invoice_order()
        for values in ({"current_agreed_fee": None}, {"agreed_fee": None}, {"fee_currency_id": None}, {"current_agreed_fee": 900}):
            with self.env.cr.savepoint() as savepoint:
                for key, value in values.items():
                    self.env.cr.execute(f"UPDATE trucalc_order SET {key}=%s WHERE id=%s", (value, order.id))
                order.invalidate_recordset()
                with self.assertRaises(ValidationError):
                    self._issue_invoice(order)
                savepoint.rollback()
        order.invalidate_recordset()
        contract = order._get_current_effective_fee()
        for invalid in ({"has_pending_request": True}, {"company_id": self.other_bank.id}, {"currency_id": 0}):
            with patch.object(type(order), "_get_current_effective_fee", return_value=dict(contract, **invalid)):
                with self.assertRaises(ValidationError):
                    self._issue_invoice(order)
        self.env.cr.execute("UPDATE trucalc_order SET agreed_fee=0,current_agreed_fee=0 WHERE id=%s", (order.id,))
        order.invalidate_recordset()
        self.assertEqual(self._issue_invoice(order).amount, 0)

    def test_renderer_and_audit_failure_are_atomic(self):
        order = self._ready_invoice_order()
        for target, method in ((self.env["trucalc.bank.invoice"], "_render_issued_pdf"),
                               (self.env["trucalc.bank.invoice"], "_create"),
                               (self.env["trucalc.order.lifecycle.event"], "_log_bank_invoice"),
                               (self.env["trucalc.order.lifecycle.event"], "_log_order_completion")):
            with patch.object(type(target), method, side_effect=ValidationError("Deliberate failure")):
                with self.assertRaises(ValidationError):
                    self._issue_invoice(order)
            self.assertFalse(self.env["trucalc.bank.invoice"].search_count([("order_id", "=", order.id)]))
            self.assertFalse(self.env["trucalc.order.lifecycle.event"].search_count([("order_id", "=", order.id), ("event_type", "=", "bank_invoice_issued")]))
            self.assertEqual(order.status, "under_review")
            self.assertFalse(self.env["trucalc.order.lifecycle.event"].search_count([("order_id", "=", order.id), ("event_type", "=", "order_completed")]))

    def test_completion_trigger_event_order_and_legacy(self):
        arch = etree.fromstring(self.env.ref('trucalc_orders.view_trucalc_order_form').arch.encode())
        self.assertFalse(arch.xpath("//button[@name='action_issue_bank_invoice']"))
        self.assertFalse(hasattr(self.env['trucalc.order'], 'action_issue_bank_invoice'))
        order = self._ready_invoice_order()
        self.assertFalse(order.bank_invoice_ids)
        invoice = self._issue_invoice(order)
        events = self.env['trucalc.order.lifecycle.event'].search([('order_id','=',order.id)], order='id')
        issue = events.filtered(lambda e:e.event_type=='bank_invoice_issued')
        complete = events.filtered(lambda e:e.event_type=='order_completed')
        self.assertEqual(len(issue),1); self.assertEqual(len(complete),1)
        self.assertLess(issue.id,complete.id)
        self.assertEqual(issue.event_at,complete.event_at)
        self.assertEqual(complete.event_at,invoice.issued_at)
        unpriced = self._ready_invoice_order()
        self.env.cr.execute('UPDATE trucalc_order SET fee_locked_at=NULL,agreed_fee=NULL,current_agreed_fee=NULL WHERE id=%s',(unpriced.id,))
        unpriced.invalidate_recordset()
        with self.assertRaisesRegex(ValidationError,'no complete original fee agreement'):
            unpriced.with_user(self.admin).action_complete_order()
        self.assertEqual(unpriced.status,'under_review')
        # Existing historical completed unpriced state has no backfill path.
        unpriced._controlled_lifecycle_write({'status':'completed'})
        with self.assertRaises(ValidationError):
            unpriced.with_user(self.admin).action_complete_order()
        self.assertFalse(unpriced.bank_invoice_ids)

    def test_latest_approval_decline_and_pending_completion(self):
        order = self._ready_invoice_order()
        for proposed, decision in ((600,'approved'),(650,'approved'),(700,'declined')):
            rid=order.with_user(self.admin).action_request_fee_change(proposed,'Scope fixture')
            with self.assertRaisesRegex(ValidationError,'pending fee'):
                order.with_user(self.admin).action_complete_order()
            self.assertFalse(order.bank_invoice_ids)
            self.env['trucalc.fee.change.request'].browse(rid).with_user(self.bank_admin)._decide(order,decision,'Declined fixture')
        invoice=self._issue_invoice(order)
        self.assertEqual(invoice.amount,650)
        self.assertEqual(order.agreed_fee,500)

    def test_paid_void_audit_and_immutability(self):
        invoice = self._issue_invoice()
        pdf = invoice.pdf_data
        wizard = self.env["trucalc.bank.invoice.status.wizard"].with_user(self.ops).create({
            "invoice_id": invoice.id, "transition": "paid", "payment_date": fields.Date.today(), "payment_reference": "  CHECK-001  "})
        wizard.action_confirm()
        self.assertEqual((invoice.status, invoice.payment_reference, invoice.paid_by), ("paid", "CHECK-001", self.ops))
        self.assertTrue(invoice.paid_at)
        for action in (lambda: invoice._mark_paid(fields.Date.today()), lambda: invoice._void("Cannot void paid")):
            with self.assertRaises(ValidationError):
                action()
        other = self._issue_invoice()
        for reason in (" ", "x" * 5001):
            with self.assertRaises(ValidationError):
                other._void(reason)
        original_pdf = other.pdf_data
        other.with_user(self.ops)._void("  Duplicate external billing  ")
        self.assertEqual(other.status, "void")
        self.assertEqual(other.void_reason, "Duplicate external billing")
        self.assertEqual(other.voided_by, self.ops)
        self.assertEqual(other.pdf_data, original_pdf)
        for action in (lambda: other._void("Again"), lambda: other._mark_paid(fields.Date.today()), lambda: self._issue_invoice(other.order_id)):
            with self.assertRaises(ValidationError):
                action()
        self.assertEqual(invoice.pdf_data, pdf)
        for item in (invoice, other):
            events = self.env["trucalc.order.lifecycle.event"].search([("bank_invoice_id", "=", item.id)])
            self.assertEqual(len(events), 2)
            for event in events:
                self.assertEqual(event.order_id, item.order_id)
                self.assertEqual(event.company_id, item.company_id)
                self.assertEqual((event.from_status, event.to_status), ("completed", "completed"))

    def test_future_payment_helper_optional_reference(self):
        invoice = self._issue_invoice()
        exact = self.env["trucalc.bank.invoice"].with_user(self.ops).search([("invoice_number", "=", invoice.order_id.order_number)])
        exact._mark_paid(str(fields.Date.today()), None)
        self.assertEqual(exact.paid_date, fields.Date.today())
        self.assertFalse(exact.payment_reference)
        self.assertEqual(exact.paid_by, self.ops)

    def test_security_raw_mutations_download_and_events(self):
        order = self._ready_invoice_order()
        for actor in (self.reviewer, self.bank_admin, self.bank_requestor, self.bank_view, self.foreign, self.vendor_user):
            with self.assertRaises(AccessError):
                self._issue_invoice(order, actor)
        invoice = self._issue_invoice(order)
        for action in (lambda: invoice.sudo().create({}), lambda: invoice.sudo().write({"amount": 1}),
                       invoice.sudo().unlink, invoice.sudo().copy,
                       lambda: invoice.with_context(invoice_workflow=True).write({"status": "paid"}),
                       lambda: order.write({"bank_invoice_ids": [Command.clear()]})):
            with self.assertRaises(AccessError):
                action()
        self.assertTrue(invoice.with_user(self.admin).has_access("read"))
        for actor in (self.reviewer, self.bank_admin, self.vendor_user, self.foreign):
            self.assertFalse(invoice.with_user(actor).has_access("read"))
            for method in (lambda: invoice.with_user(actor)._mark_paid(fields.Date.today()), lambda: invoice.with_user(actor)._void("No")):
                with self.assertRaises(AccessError):
                    method()
        with self.assertRaises(AccessError):
            self.env["ir.binary"].with_user(self.admin)._find_record(res_model=invoice._name, res_id=invoice.id, field="pdf_data")
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(self.admin)._render_qweb_html("trucalc_orders.action_bank_invoice_report", invoice.ids)
        visible = self.env["trucalc.order.lifecycle.event"].with_user(self.reviewer).search([("order_id", "=", order.id)])
        self.assertTrue(visible)
        self.assertFalse(visible.filtered(lambda e: e.event_type.startswith("bank_invoice_")))
        event = self.env["trucalc.order.lifecycle.event"].search([("bank_invoice_id", "=", invoice.id)])
        self.assertFalse(event.with_user(self.reviewer).has_access("read"))
        for actor in (self.bank_admin, self.bank_requestor, self.bank_view):
            values = self.env["trucalc.bank.invoice"].with_user(actor)._portal_values(order)
            self.assertEqual(values["number"], order.order_number)
        with self.assertRaises(AccessError):
            self.env["trucalc.bank.invoice"].with_user(self.foreign)._bank_invoice(order, invoice.id)
        self.ops.active = False
        with self.assertRaises(AccessError):
            invoice.with_user(self.ops)._mark_paid(fields.Date.today())
        with patch.object(type(self.admin), "_trucalc_has_external_role", return_value=True):
            with self.assertRaises(AccessError):
                invoice._void("Mixed role")


@tagged("post_install", "-at_install", "trucalc_bank_invoice_portal")
class TestBankInvoicePortal(BankInvoiceFixtures, HttpCase):
    def test_bank_download_statuses_and_denials(self):
        invoice = self._issue_invoice()
        order = invoice.order_id
        detail = "/my/trucalc/bank/orders/%s/documents" % order.order_number
        download = "/my/trucalc/bank/orders/%s/invoice/%s/download" % (order.order_number, invoice.id)
        pdf = base64.b64decode(invoice.pdf_data)
        for actor in (self.bank_admin, self.bank_requestor, self.bank_view):
            self.authenticate(actor.login, self.password)
            page = self.url_open(detail)
            self.assertEqual(page.status_code, 200)
            self.assertIn("Bank Invoice", page.text)
            self.assertIn(download, page.text)
            for _ in range(2):
                response = self.url_open(download)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, pdf)
            self.assertNotIn("Mark Paid", page.text)
            self.assertNotIn("Void Invoice", page.text)
        for actor in (self.foreign, self.vendor_user, self.reviewer):
            self.authenticate(actor.login, self.password)
            self.assertEqual(self.url_open(download).status_code, 404)
        self.authenticate(self.bank_admin.login, self.password)
        for route in ("/web/content?model=trucalc.bank.invoice&id=%s&field=pdf_data" % invoice.id,
                      "/report/pdf/trucalc_orders.bank_invoice_pdf/%s" % invoice.id,
                      "/report/html/trucalc_orders.bank_invoice_pdf/%s" % invoice.id):
            self.assertIn(self.url_open(route).status_code, (403, 404))
        invoice._mark_paid(fields.Date.today(), "Check-HTTP")
        self.assertIn("Paid", self.url_open(detail).text)
        self.assertEqual(self.url_open(download).content, pdf)
        other = self._issue_invoice()
        other._void("Owner requested void")
        detail2 = "/my/trucalc/bank/orders/%s/documents" % other.order_id.order_number
        self.assertIn("Void", self.url_open(detail2).text)
