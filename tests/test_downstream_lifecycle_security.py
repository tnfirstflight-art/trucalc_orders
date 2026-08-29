from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_downstream_lifecycle_security")
class TestDownstreamLifecycleSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "4D Other Company"})
        cls.bank = cls.env["res.company"].create({"name": "4D Bank"})
        cls.admin = cls._user("admin", "group_trucalc_admin")
        cls.ops = cls._user("ops", "group_trucalc_operations")
        cls.reviewer = cls._user("reviewer", "group_trucalc_reviewer")
        cls.other_reviewer = cls._user("other-reviewer", "group_trucalc_reviewer")
        cls.cross_company_reviewer = cls._user(
            "cross-company-reviewer", "group_trucalc_reviewer",
            companies=cls.other_company,
        )
        cls.bank_admin = cls._user(
            "bank-admin", "group_bank_admin", bank=cls.bank,
        )
        cls.bank_requestor = cls._user(
            "bank-requestor", "group_bank_requestor", bank=cls.bank,
        )
        cls.bank_viewer = cls._user(
            "bank-viewer", "group_bank_view_only", bank=cls.bank,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "4D Reviewer Entity", "vendor_type": "reviewer",
        })
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": cls.vendor.id, "service_type": "review", "fee": 250,
        })
        cls.vendor_user = cls._user(
            "vendor", "group_vendor_portal", vendor=cls.vendor,
        )

    @classmethod
    def _user(cls, suffix, group, bank=False, vendor=False, companies=False):
        companies = companies or cls.company
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4D %s" % suffix,
            "login": "4d-%s@example.test" % suffix,
            "email": "4d-%s@example.test" % suffix,
            "company_id": companies.id,
            "company_ids": [Command.set(companies.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.%s" % group).id,
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _order(self):
        return self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4D Borrower",
            "property_address": "4 Lifecycle Way",
            "company_id": self.company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })

    def _ready_for_assignment(self, reviewer=None):
        order = self._order()
        order.with_user(self.admin).write({
            "reviewer_user_id": (reviewer or self.reviewer).id,
        })
        order.with_user(self.admin)._controlled_lifecycle_write({
            "status": "report_received",
        })
        return order

    def test_ordinary_status_write_is_denied_for_every_persona(self):
        order = self._order()
        for user in (
            self.admin, self.ops, self.reviewer, self.bank_admin,
            self.bank_requestor, self.bank_viewer, self.vendor_user,
        ):
            with self.assertRaises(AccessError), self.cr.savepoint():
                order.with_user(user).write({"status": "completed"})
        self.assertEqual(order.status, "new")

    def test_assignment_is_exact_state_locked_and_creates_one_event(self):
        order = self._ready_for_assignment()
        self.assertTrue(order.with_user(self.ops).action_assign_reviewer())
        event = self.env["trucalc.order.lifecycle.event"].search([
            ("order_id", "=", order.id),
        ])
        self.assertEqual(order.status, "reviewer_assigned")
        self.assertEqual(event.order_id, order)
        self.assertEqual(event.stable_order_id, order.id)
        self.assertEqual((event.from_status, event.to_status), (
            "report_received", "reviewer_assigned",
        ))
        self.assertEqual(event.actor_id, self.ops)
        self.assertEqual(event.company_id, order.company_id)
        self.assertTrue(event.event_at)
        self.assertFalse(order.reviewer_id)
        self.assertEqual(order.review_fee, 0.0)
        self.assertFalse(event.reviewer_id)
        self.assertEqual(event.reviewer_user_id, self.reviewer)
        with self.assertRaises(ValidationError):
            order.with_user(self.ops).action_assign_reviewer()
        self.assertEqual(len(order.lifecycle_event_ids), 1)

    def test_internal_assignment_clears_stale_external_review_values(self):
        order = self._ready_for_assignment()
        order.with_user(self.admin).write({
            "reviewer_id": self.vendor.id,
            "review_fee": 250.0,
        })
        order.with_user(self.admin).action_assign_reviewer()
        event = order.lifecycle_event_ids
        self.assertFalse(order.reviewer_id)
        self.assertEqual(order.review_fee, 0.0)
        self.assertFalse(event.reviewer_id)
        self.assertEqual(event.reviewer_user_id, self.reviewer)

    def test_assignment_rejects_multi_record_invocation_atomically(self):
        first = self._ready_for_assignment()
        second = self._ready_for_assignment()
        with self.assertRaises(ValueError):
            (first | second).with_user(self.admin).action_assign_reviewer()
        self.assertEqual((first.status, second.status), (
            "report_received", "report_received",
        ))
        self.assertFalse((first | second).lifecycle_event_ids)

    def test_assignment_requires_admin_or_operations(self):
        for user in (self.reviewer, self.bank_admin, self.vendor_user):
            order = self._ready_for_assignment()
            with self.assertRaises(AccessError):
                order.with_user(user).action_assign_reviewer()
            self.assertEqual(order.status, "report_received")
            self.assertFalse(order.lifecycle_event_ids)

    def test_reviewer_identity_is_strict_and_company_compatible(self):
        order = self._order()
        for invalid in (
            self.admin, self.bank_admin, self.vendor_user,
            self.cross_company_reviewer,
        ):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                order.with_user(self.admin).write({"reviewer_user_id": invalid.id})
        self.reviewer.active = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.with_user(self.admin).write({"reviewer_user_id": self.reviewer.id})
        self.reviewer.active = True
        order.with_user(self.admin).write({"reviewer_user_id": self.reviewer.id})
        self.assertEqual(order.reviewer_user_id, self.reviewer)

    def test_reviewer_reads_only_own_eligible_status_orders(self):
        assigned = self._ready_for_assignment()
        assigned.with_user(self.admin).action_assign_reviewer()
        unassigned = self._ready_for_assignment(self.other_reviewer)
        unassigned.with_user(self.admin).action_assign_reviewer()
        new_order = self._order()
        reviewer_model = self.env["trucalc.order"].with_user(self.reviewer)
        self.assertEqual(reviewer_model.search([("id", "in", (
            assigned.id, unassigned.id, new_order.id,
        ))]), assigned.with_user(self.reviewer))
        with self.assertRaises(AccessError):
            unassigned.with_user(self.reviewer).read(["status"])
        self.assertFalse(assigned.with_user(self.reviewer).has_access("write"))

    def test_reviewer_home_action_and_restricted_application_shell(self):
        home_action = self.env.ref("trucalc_orders.action_trucalc_orders")
        self.assertEqual(self.reviewer.action_id.id, home_action.id)
        self.assertFalse(self.admin.action_id)
        self.assertFalse(self.ops.action_id)

        menu_model = self.env["ir.ui.menu"].with_user(self.reviewer)
        loaded = menu_model.load_menus(False)
        restricted_xmlids = (
            "mail.menu_root_discuss",
            "project_todo.menu_todo_todos",
            "contacts.menu_contacts",
            "base.menu_management",
        )
        for xmlid in restricted_xmlids:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                self.assertNotIn(menu.id, loaded)
        self.assertIn(self.env.ref("trucalc_orders.menu_trucalc_root").id, loaded)

        for user in (self.admin, self.ops):
            loaded = self.env["ir.ui.menu"].with_user(user).load_menus(False)
            for xmlid in restricted_xmlids:
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu and menu.with_user(user)._filter_visible_menus():
                    self.assertIn(menu.id, loaded)

    def test_existing_reviewer_home_action_is_corrected_idempotently(self):
        home_action = self.env.ref("trucalc_orders.action_trucalc_orders")
        self.reviewer.sudo().with_context(trucalc_home_action_sync=True).write({
            "action_id": False,
        })
        self.assertFalse(self.reviewer.action_id)
        self.reviewer._trucalc_sync_home_action()
        self.assertEqual(self.reviewer.action_id.id, home_action.id)
        self.reviewer._trucalc_sync_home_action()
        self.assertEqual(self.reviewer.action_id.id, home_action.id)

    def test_reviewer_partner_rule_is_an_effective_allowlist(self):
        order = self._ready_for_assignment()
        order.with_user(self.admin).action_assign_reviewer()
        order.with_user(self.admin).message_post(body="Reviewer-visible audit context")

        allowed = (
            self.reviewer.partner_id
            | self.env.ref("base.partner_root")
            | self.company.partner_id
            | self.admin.partner_id
        )
        unrelated = (
            self.other_reviewer.partner_id
            | self.bank_admin.partner_id
            | self.bank.partner_id
            | self.vendor_user.partner_id
            | self.other_company.partner_id
        )
        partners = self.env["res.partner"].with_user(
            self.reviewer
        ).with_context(active_test=False)
        visible = partners.search([("id", "in", (allowed | unrelated).ids)])
        self.assertEqual(visible, allowed.with_user(self.reviewer))
        for partner in unrelated:
            with self.assertRaises(AccessError):
                partner.with_user(self.reviewer).read(["name"])

        values = order.with_user(self.reviewer).read([
            "order_number", "company_id", "requestor_id", "reviewer_user_id",
        ])[0]
        self.assertEqual(values["company_id"][0], self.company.id)
        self.assertEqual(values["requestor_id"][0], self.admin.id)
        self.assertEqual(values["reviewer_user_id"][0], self.reviewer.id)
        messages = order.message_ids.with_user(self.reviewer).read(["author_id"])
        self.assertTrue(messages)
        self.assertIn(self.admin.partner_id.id, {
            value["author_id"][0] for value in messages if value["author_id"]
        })

    def test_reviewer_collaborates_on_assigned_readonly_order(self):
        order = self._ready_for_assignment()
        order.with_user(self.admin).action_assign_reviewer()
        reviewer_order = order.with_user(self.reviewer)

        self.assertEqual(
            reviewer_order._mail_get_operation_for_mail_message_operation(
                "create"
            )[reviewer_order],
            "read",
        )
        self.assertFalse(reviewer_order.has_access("write"))
        odoobot = self.env.ref("base.partner_root")
        self.assertEqual(
            odoobot.with_user(self.reviewer).read(["name"])[0]["name"],
            "OdooBot",
        )

        message = reviewer_order.message_post(
            body="Assigned Reviewer message",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        note = reviewer_order.message_post(
            body="Assigned Reviewer internal note",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        self.assertEqual(message.author_id, self.reviewer.partner_id)
        self.assertEqual(note.author_id, self.reviewer.partner_id)

        activity = reviewer_order.activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=self.reviewer.id,
            summary="Assigned Reviewer follow-up",
        )
        self.assertEqual(activity.user_id, self.reviewer)
        activity.with_user(self.reviewer).write({
            "summary": "Assigned Reviewer updated follow-up",
        })
        self.assertEqual(activity.summary, "Assigned Reviewer updated follow-up")
        activity.with_user(self.reviewer).unlink()
        self.assertFalse(activity.exists())

        self.assertEqual(order.status, "reviewer_assigned")
        self.assertFalse(reviewer_order.has_access("write"))

    def test_reviewer_retains_no_module_management_authority(self):
        modules = self.env["ir.module.module"].with_user(self.reviewer)
        self.assertTrue(modules.has_access("read"))
        self.assertFalse(modules.has_access("create"))
        self.assertFalse(modules.has_access("write"))
        self.assertFalse(modules.has_access("unlink"))
        self.assertFalse(self.reviewer.has_group("base.group_system"))

    def test_reassignment_transfers_reviewer_visibility(self):
        order = self._ready_for_assignment()
        order.with_user(self.admin).action_assign_reviewer()
        self.assertTrue(order.with_user(self.reviewer).has_access("read"))
        order.with_user(self.admin).write({"reviewer_user_id": self.other_reviewer.id})
        self.assertFalse(order.with_user(self.reviewer).has_access("read"))
        self.assertTrue(order.with_user(self.other_reviewer).has_access("read"))

    def test_blocked_downstream_actions_have_no_side_effects(self):
        order = self._ready_for_assignment()
        authorization_count = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().search_count([("order_id", "=", order.id)])
        for method in (
            "action_report_received", "action_start_review",
            "action_complete_review", "action_cancelled",
        ):
            with self.assertRaises(AccessError):
                getattr(order.with_user(self.admin), method)()
            self.assertEqual(order.status, "report_received")
            self.assertFalse(order.lifecycle_event_ids)
            self.assertEqual(self.env[
                "trucalc.order.vendor.authorization"
            ].sudo().search_count([("order_id", "=", order.id)]), authorization_count)

    def test_lifecycle_events_are_immutable_and_internal_only(self):
        order = self._ready_for_assignment()
        order.with_user(self.admin).action_assign_reviewer()
        event = self.env["trucalc.order.lifecycle.event"].search([
            ("order_id", "=", order.id),
        ])
        self.assertTrue(event.with_user(self.admin).has_access("read"))
        self.assertTrue(event.with_user(self.ops).has_access("read"))
        for user in (
            self.reviewer, self.bank_admin, self.bank_requestor,
            self.bank_viewer, self.vendor_user,
        ):
            self.assertFalse(event.with_user(user).has_access("read"))
        with self.assertRaises(AccessError):
            event.with_user(self.admin).write({"to_status": "completed"})
        with self.assertRaises(AccessError):
            event.with_user(self.admin).unlink()
        with self.assertRaises(AccessError):
            self.env["trucalc.order.lifecycle.event"].with_user(
                self.admin
            ).create({
                "order_id": event.order_id.id,
                "stable_order_id": event.order_id.id,
                "company_id": event.company_id.id,
                "event_type": "reviewer_assigned",
                "from_status": "new",
                "to_status": "completed",
                "actor_id": self.admin.id,
                "event_at": fields.Datetime.now(),
            })

    def test_bank_order_acl_is_read_create_without_write_or_unlink(self):
        for user in (self.bank_admin, self.bank_requestor):
            model = self.env["trucalc.order"].with_user(user)
            self.assertTrue(model.has_access("read"))
            self.assertTrue(model.has_access("create"))
            self.assertFalse(model.has_access("write"))
            self.assertFalse(model.has_access("unlink"))
        model = self.env["trucalc.order"].with_user(self.bank_viewer)
        self.assertTrue(model.has_access("read"))
        self.assertFalse(model.has_access("create"))
        self.assertFalse(model.has_access("write"))
        self.assertFalse(model.has_access("unlink"))

        bank_order = self.env["trucalc.order"].with_user(
            self.bank_requestor
        ).create({
            "borrower": "4D Bank Borrower",
            "property_address": "4 Bank Way",
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })
        self.assertEqual(bank_order.company_id, self.bank)
        self.assertEqual(bank_order.requestor_company_id, self.bank)
        self.assertEqual(bank_order.requestor_id, self.bank_requestor)
        for method in (
            "action_report_received", "action_assign_reviewer",
            "action_start_review", "action_complete_review", "action_cancelled",
        ):
            with self.assertRaises(AccessError):
                getattr(bank_order, method)()

    def test_lifecycle_event_company_rule_is_not_permissive(self):
        self.admin.write({
            "company_ids": [Command.link(self.other_company.id)],
        })
        order = self.env["trucalc.order"].with_user(self.admin).with_context(
            allowed_company_ids=(self.company | self.other_company).ids,
        ).create({
            "borrower": "4D Other Company Borrower",
            "property_address": "4 Other Company Way",
            "company_id": self.other_company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
            "reviewer_user_id": self.cross_company_reviewer.id,
        })
        order.with_user(self.admin)._controlled_lifecycle_write({
            "status": "report_received",
        })
        order.with_user(self.admin).action_assign_reviewer()
        event = self.env["trucalc.order.lifecycle.event"].search([
            ("order_id", "=", order.id),
        ])
        self.assertEqual(event.company_id, self.other_company)
        self.assertFalse(event.with_user(self.ops).has_access("read"))

    def test_reopen_bidding_remains_available(self):
        order = self._order()
        order.with_user(self.admin)._controlled_lifecycle_write({
            "status": "assigned", "bidding_round": 1,
        })
        self.assertTrue(order.with_user(self.ops).action_reopen_bidding())
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 2))
