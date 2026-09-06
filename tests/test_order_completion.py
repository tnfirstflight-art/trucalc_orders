from unittest.mock import patch
from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged

from .test_review_workflow import TestControlledValuationReview, PDF
from .test_vendor_deliverables import TestVendorDeliverablePortal


@tagged("post_install", "-at_install", "trucalc_order_completion")
class TestOrderCompletion(TestControlledValuationReview):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Completion now issues a Bank invoice: use a distinct customer Bank.
        cls.company = cls.env['res.company'].create({'name': 'Completion Customer Bank'})
        for actor in (cls.admin, cls.ops):
            actor.write({'company_ids': [Command.link(cls.company.id)]})

    def _valuation_order(self):
        order, authorization, valuation = super()._valuation_order()
        area = self.env['trucalc.service.area'].with_user(self.admin).create({
            'state_id': self.env.ref('base.state_us_37').id,
            'county': 'Completion fixture %s' % order.id,
            'service_type': 'evaluation', 'base_fee': 500,
        })
        # Isolated historical fixture; never changes persistent historical pricing.
        order._controlled_lifecycle_write({
            'service_area_id': area.id, 'fee_currency_id': area.currency_id.id,
            'agreed_fee': 500, 'current_agreed_fee': 500,
            'fee_source': 'base', 'fee_locked_at': fields.Datetime.now(),
        })
        return order, authorization, valuation

    def test_order_date_and_company_view_contracts(self):
        view = "trucalc_orders.view_trucalc_order_form"
        new_form = Form(self.env["trucalc.order"].with_user(self.admin), view=view)
        with self.assertRaises(AssertionError):
            new_form.order_date_display = "01/01/2000"
        order, _valuation, _invoice = self._ready()
        for completed in (False, True):
            if completed:
                order.with_user(self.admin).action_complete_order()
            form = Form(order.with_user(self.admin), view=view)
            with self.assertRaises(AssertionError):
                form.order_date_display = "01/01/2000"
            with self.assertRaises(AssertionError):
                form.company_id = self.other_company
        arch = etree.fromstring(self.env.ref(view).arch)
        self.assertEqual(
            arch.xpath(
                "//group[@string='Order / Request Information']/field[@name='company_id']"
            )[0].get("readonly"),
            "fee_locked_at or (create_date and not is_internal_draft)",
        )

    def _ready(self, invoice=True, approve=True):
        order, authorization, valuation = self._valuation_order()
        bill = self.env["trucalc.vendor.deliverable"].browse()
        if invoice:
            bill = self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)._submit(
                authorization, self.vendor_user, "vendor_invoice", "Closeout Invoice.pdf", PDF,
            )
        self._assign_and_accept(order)
        if approve:
            order.with_user(self.reviewer).action_approve_valuation(valuation)
        return order, valuation, bill

    def test_completion_authority_audit_and_immutability(self):
        for actor in (self.admin, self.ops):
            order, valuation, invoice = self._ready()
            before = (valuation.read(), invoice.read())
            order.with_user(actor).action_complete_order()
            self.assertEqual(order.status, "completed")
            event = order.lifecycle_event_ids.filtered(lambda e: e.event_type == "order_completed")
            self.assertEqual(len(event), 1)
            self.assertEqual(event.target_valuation_id, valuation)
            self.assertEqual(event.actor_id, actor)
            self.assertEqual(event.reviewer_user_id, self.reviewer)
            self.assertEqual((event.from_status, event.to_status), ("under_review", "completed"))
            self.assertTrue(event.event_at)
            self.assertEqual((valuation.read(), invoice.read()), before)
            with self.assertRaises(ValidationError):
                order.with_user(actor).action_complete_order()
            for operation in (lambda: event.write({"event_at": fields.Datetime.now()}), event.unlink):
                with self.assertRaises(AccessError):
                    operation()

    def test_completion_unauthorized_and_company_scope(self):
        order, _valuation, _invoice = self._ready()
        ordinary = self._user("4d4-ordinary", [])
        for actor in (ordinary, self.vendor_user, self.reviewer):
            # Assigned Reviewer is Operations, but belongs to another company.
            with self.assertRaises(AccessError):
                order.with_user(actor).action_complete_order()
        # Reviewer is additive; simulate lack of the normal completion role without
        # creating an invalid persistent persona (Reviewer-only provisioning is forbidden).
        original = type(self.reviewer).has_group
        def no_completion_role(user, group):
            if group in ("trucalc_orders.group_trucalc_admin", "trucalc_orders.group_trucalc_operations"):
                return False
            return original(user, group)
        with patch.object(type(self.reviewer), "has_group", no_completion_role):
            with self.assertRaises(AccessError):
                order.with_user(self.reviewer).action_complete_order()
        self.assertEqual(order.status, "under_review")

    def test_same_person_can_approve_and_complete(self):
        actor = self._user("4d4-admin-reviewer", ["group_trucalc_admin", "group_trucalc_reviewer"])
        order, authorization, valuation = self._valuation_order()
        self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)._submit(
            authorization, self.vendor_user, "vendor_invoice", "Invoice.pdf", PDF,
        )
        self._assign_and_accept(order, actor)
        order.with_user(actor).action_approve_valuation(valuation)
        order.with_user(actor).action_complete_order()
        self.assertEqual(order.status, "completed")

    def test_completion_requires_invoice_and_approval(self):
        order, valuation, _invoice = self._ready(invoice=False)
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_complete_order()
        order2, target, _invoice2 = self._ready(approve=False)
        with self.assertRaises(ValidationError):
            order2.with_user(self.admin).action_complete_order()
        order2.with_user(self.reviewer).action_request_valuation_revision(target, "Revise.")
        with self.assertRaises(ValidationError):
            order2.with_user(self.admin).action_complete_order()
        self.assertEqual(order.status, "under_review")

    def test_completion_requires_current_acceptance_cycle(self):
        order, valuation, _invoice = self._ready()
        # Adversarial isolated history: a later assignment must invalidate old acceptance.
        self.env["trucalc.order.lifecycle.event"]._log_event(
            order, "reviewer_assigned", "report_received", "reviewer_assigned", self.admin,
        )
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_complete_order()
        self.assertEqual(order.status, "under_review")

    def test_completion_rejects_missing_current_reviewer_and_duplicate_event(self):
        for field_values in ({"status": "report_received"}, {"reviewer_user_id": False}):
            order, _valuation, _invoice = self._ready()
            order._controlled_lifecycle_write(field_values)
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()
        order, valuation, _invoice = self._ready()
        self.env["trucalc.order.lifecycle.event"]._log_order_completion(order, valuation, self.admin)
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_complete_order()
        order, _authorization, _valuation = self._valuation_order()
        self._assign_and_accept(order)
        # Empty current lookup must fail closed, without altering immutable data.
        model = type(self.env["trucalc.vendor.deliverable"])
        with patch.object(model, "search", return_value=self.env["trucalc.vendor.deliverable"].browse()):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()

    def test_completion_rejects_stale_approval_and_invoice_provenance(self):
        order, valuation, invoice = self._ready()
        other, other_valuation, other_invoice = self._ready()
        Event = type(self.env["trucalc.order.lifecycle.event"])
        other_approval = self.env["trucalc.order.lifecycle.event"]._valuation_approval(other_valuation)
        with patch.object(Event, "_valuation_approval", return_value=other_approval):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()
        with patch.object(Event, "_open_valuation_revision_request", return_value=other_approval):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()
        model = type(invoice)
        search = model.search
        def wrong_invoice(records, domain, *args, **kwargs):
            if ("artifact_type", "=", "vendor_invoice") in domain:
                return other_invoice
            return search(records, domain, *args, **kwargs)
        with patch.object(model, "search", wrong_invoice):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()
        self.assertEqual(order.status, "under_review")

    def test_historical_approver_need_not_remain_active(self):
        order, _valuation, _invoice = self._ready()
        self.reviewer.active = False
        order.with_user(self.admin).action_complete_order()
        self.assertEqual(order.status, "completed")

    def test_completion_event_failure_rolls_back(self):
        order, _valuation, _invoice = self._ready()
        Event = type(self.env["trucalc.order.lifecycle.event"])
        with patch.object(Event, "_log_order_completion", side_effect=ValidationError("test failure")):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_complete_order()
        order.invalidate_recordset()
        self.assertEqual(order.status, "under_review")
        self.assertFalse(order.lifecycle_event_ids.filtered(lambda e: e.event_type == "order_completed"))

    def test_completed_business_and_document_freeze(self):
        order, valuation, invoice = self._ready()
        Document = self.env["trucalc.document"].with_user(self.admin)
        tag = self.env["trucalc.document.tag"].search([], limit=1)
        values = {"order_id": order.id, "filename": "Supporting.pdf", "attachment": PDF, "tag_id": tag.id}
        document = Document.create(values)
        order.with_user(self.admin).action_complete_order()
        operations = [
            lambda: order.with_user(self.admin).write({"borrower": "Changed"}),
            lambda: order.with_user(self.ops).write({"notes": "Changed"}),
            lambda: order.sudo().write({"status": "under_review"}),
            lambda: Document.create(dict(values, filename="Extra.pdf")),
            lambda: document.write({"tag_id": tag.id}),
            lambda: document.write({"visible_after_engagement": True}),
            document.action_controlled_delete,
            lambda: order.with_user(self.admin).action_add_document(),
        ]
        for operation in operations:
            with self.assertRaises(AccessError):
                operation()
        self.assertTrue(document.attachment)
        arch = etree.fromstring(self.env.ref("trucalc_orders.view_trucalc_order_form").arch)
        buttons = arch.xpath("//field[@name='document_ids']//button")
        context = {"active": True, "parent": type("Parent", (), {"status": "completed"})()}
        for name, hidden in (("action_download", False), ("action_controlled_delete", True)):
            button = next(node for node in buttons if node.get("name") == name)
            self.assertEqual(bool(eval(button.get("invisible"), {"__builtins__": {}}, context)), hidden)
        for actor in (self.admin, self.ops):
            self.assertEqual(document.with_user(actor).action_download()["type"], "ir.actions.act_url")
        self.assertTrue(invoice.with_user(self.admin).has_access("read"))
        self.assertTrue(valuation.with_user(self.ops).has_access("read"))

    def test_direct_assignment_context_cannot_bypass(self):
        order, _valuation, _invoice = self._ready()
        with self.assertRaises(AccessError):
            order.with_user(self.admin).with_context(trucalc_controlled_reviewer_assignment=True).write({
                "reviewer_user_id": self.other_reviewer.id,
            })

    def test_completed_vendor_reviewer_history_and_actions(self):
        order, valuation, invoice = self._ready()
        order.with_user(self.admin).action_complete_order()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(self.vendor_user).search([
            ("order_number", "=", order.order_number),
        ])
        self.assertEqual(len(projection), 1)
        self.assertEqual(projection.order_status, "completed")
        for item in (valuation, invoice):
            self.assertEqual(item.with_user(self.vendor_user)._authorize_download(self.vendor_user), item)
        self.assertTrue(order.with_user(self.reviewer).has_access("read"))
        self.assertEqual(valuation.with_user(self.reviewer)._authorize_download(self.reviewer), valuation)
        self.assertFalse(invoice.with_user(self.reviewer).has_access("read"))
        history = order.lifecycle_event_ids
        self.assertTrue(history.filtered(lambda e:not e.event_type.startswith('bank_invoice_')).with_user(self.reviewer).has_access("read"))
        self.assertFalse(history.filtered(lambda e:e.event_type.startswith('bank_invoice_')).with_user(self.reviewer).has_access("read"))
        self.assertEqual(order.with_user(self.reviewer).action_view_previous_valuations()["res_model"], valuation._name)
        for operation in (
            lambda: order.with_user(self.reviewer).action_start_review(),
            lambda: order.with_user(self.reviewer).action_request_valuation_revision(valuation, "Late"),
            lambda: order.with_user(self.reviewer).action_approve_valuation(valuation),
            lambda: order.with_user(self.admin).action_reassign_reviewer(self.other_reviewer, "Late"),
        ):
            with self.assertRaises(ValidationError):
                operation()
        for artifact in ("valuation", "vendor_invoice"):
            with self.assertRaises(AccessError):
                self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)._submit(
                    valuation.authorization_id, self.vendor_user, artifact, "Late.pdf", PDF,
                )
        with self.assertRaises(AccessError):
            projection.action_vendor_accept_engagement()


@tagged("post_install", "-at_install", "trucalc_order_completion_portal")
class TestOrderCompletionPortal(TestVendorDeliverablePortal):
    def test_real_completion_releases_only_valuation_and_freezes_uploads(self):
        customer = self.env['res.company'].create({'name':'Completion Portal Customer'})
        for actor in (self.admin,self.reviewer,self.vendor_user):
            actor.write({'company_ids':[Command.link(customer.id)]})
        self.bank = self._user('completion-portal-bank','group_bank_admin',bank=customer)
        self.env.ref('trucalc_orders.seq_trucalc_order').write({'company_id':False})
        self.env = self.env(context=dict(self.env.context,allowed_company_ids=[customer.id]))
        order, valuation = self._bank_release_fixture()
        area = self.env['trucalc.service.area'].with_user(self.admin).create({
            'state_id': self.env.ref('base.state_us_37').id,
            'county': 'Portal completion fixture %s' % order.id,
            'service_type': 'evaluation', 'base_fee': 500,
        })
        order._controlled_lifecycle_write({'service_area_id':area.id, 'fee_currency_id':area.currency_id.id,
            'agreed_fee':500,'current_agreed_fee':500,'fee_source':'base','fee_locked_at':fields.Datetime.now()})
        invoice = self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)._submit(
            valuation.authorization_id, self.vendor_user, "vendor_invoice", "Closeout Invoice.pdf", PDF,
        )
        order.with_user(self.reviewer).action_approve_valuation(valuation)
        self._login(self.bank)
        self._assert_bank_valuation_hidden(order, valuation)
        with self.assertRaises(AccessError):
            order.with_user(self.bank).action_complete_order()
        order.with_user(self.admin).action_complete_order()
        page = self.url_open(f"/my/trucalc/bank/orders/{order.order_number}/documents").text
        self.assertIn("Completed", page)
        self.assertIn(valuation.filename, page)
        self.assertNotIn(invoice.filename, page)
        self.assertNotIn('name="document_file"', page)
        self.assertEqual(self.url_open(self._bank_valuation_url(order, valuation)).status_code, 200)
        self.assertEqual(self.url_open(self._bank_valuation_url(order, invoice)).status_code, 404)
        self._assert_bank_generic_download_denied(valuation)
        self.assertEqual(self.url_open(
            f"/my/trucalc/bank/orders/{order.order_number}/documents/upload",
            data={"csrf_token": self._csrf_token(), "draft_action": "upload"},
        ).status_code, 404)
        self._login(self.vendor_user)
        page = self.url_open(f"/my/trucalc/orders/{order.order_number}").text
        self.assertIn("Completed", page)
        self.assertIn(valuation.filename, page)
        self.assertIn(invoice.filename, page)
        self.assertNotIn('name="valuation_file"', page)
        self.assertNotIn('name="vendor_invoice_file"', page)
