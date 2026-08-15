from datetime import timedelta
from queue import Queue
from threading import Thread
import time
import unittest
from psycopg2.errors import SerializationFailure

from odoo import api, Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.sql_db import db_connect


@tagged("post_install", "-at_install", "trucalc_bid_lifecycle")
class TestBidLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_group = cls.env.ref("trucalc_orders.group_trucalc_admin")
        cls.ops_group = cls.env.ref("trucalc_orders.group_trucalc_operations")
        cls.reviewer_group = cls.env.ref("trucalc_orders.group_trucalc_reviewer")
        cls.vendor_group = cls.env.ref("trucalc_orders.group_vendor_portal")
        cls.bank_groups = [
            cls.env.ref("trucalc_orders.group_bank_admin"),
            cls.env.ref("trucalc_orders.group_bank_requestor"),
            cls.env.ref("trucalc_orders.group_bank_view_only"),
        ]
        cls.vendor_a = cls.env["trucalc.vendor"].create(
            {"name": "4A3 Vendor A", "vendor_type": "appraiser"}
        )
        cls.vendor_b = cls.env["trucalc.vendor"].create(
            {"name": "4A3 Vendor B", "vendor_type": "appraiser"}
        )
        cls.reviewer_vendor = cls.env["trucalc.vendor"].create(
            {"name": "4A3 Reviewer", "vendor_type": "reviewer"}
        )
        cls.admin = cls._user("4a3-admin", cls.admin_group)
        cls.ops = cls._user("4a3-ops", cls.ops_group)
        cls.reviewer = cls._user("4a3-reviewer", cls.reviewer_group)
        cls.vendor_user_a = cls._user("4a3-vendor-a", cls.vendor_group, cls.vendor_a)
        cls.vendor_user_b = cls._user("4a3-vendor-b", cls.vendor_group, cls.vendor_b)
        cls.unmapped_vendor = cls._user("4a3-vendor-none", cls.vendor_group)
        cls.mixed_user = cls._user("4a3-mixed", cls.vendor_group, cls.vendor_a)
        # Normal Odoo validation makes Portal/User groups mutually exclusive.
        # Simulate corrupted/imported membership to verify the server fails closed.
        cls.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (cls.mixed_user.id, cls.ops_group.id),
        )
        cls.mixed_user.invalidate_recordset()
        cls.bank_users = [
            cls._user("4a3-bank-%s" % index, group)
            for index, group in enumerate(cls.bank_groups)
        ]

    @classmethod
    def _user(cls, login, group, vendor=False, extra_group=False):
        groups = [group.id]
        if extra_group:
            groups.append(extra_group.id)
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": "%s@example.test" % login,
            "group_ids": [Command.set(groups)],
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _order(self, user=None):
        env = self.env(user=user or self.admin)
        return env["trucalc.order"].create({
            "borrower": "Lifecycle Test",
            "property_address": "1 Test Way",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
        })

    def _invitation(self, vendor=None, order=None, manager=None, deadline=False):
        manager = manager or self.admin
        order = order or self._order(manager)
        if order.status == "new":
            order.with_user(manager).action_bid_requested()
        values = {"order_id": order.id, "vendor_id": (vendor or self.vendor_a).id}
        if deadline:
            values["response_deadline"] = deadline
        return self.env["trucalc.bid.invitation"].with_user(manager).create(values)

    def _draft(self, invitation, user=None, **overrides):
        values = {
            "option_name": "Standard", "bid_amount": 500.0,
            "turn_time_days": 5, **overrides,
        }
        return invitation.with_user(user or self.vendor_user_a).action_vendor_create_option(values)

    def test_authority_start_and_invitation(self):
        for manager in (self.admin, self.ops):
            order = self._order(manager)
            order.with_user(manager).action_bid_requested()
            self.assertEqual((order.status, order.bidding_round), ("bid_requested", 1))
            self.env["trucalc.bid.invitation"].with_user(manager).create(
                {"order_id": order.id, "vendor_id": self.vendor_a.id}
            )
        for denied in [self.reviewer, self.vendor_user_a, *self.bank_users]:
            with self.assertRaises(AccessError):
                self._order().with_user(denied).action_bid_requested()

    def test_vendor_identity_and_persona_fail_closed(self):
        invitation = self._invitation()
        for user in (self.vendor_user_b, self.unmapped_vendor, self.mixed_user):
            with self.assertRaises(AccessError):
                invitation.with_user(user).action_vendor_create_option(
                    {"option_name": "X", "bid_amount": 1, "turn_time_days": 1}
                )
        nonexistent = self.env["trucalc.bid.invitation"].browse(999999999)
        with self.assertRaises(AccessError):
            nonexistent.with_user(self.vendor_user_a).action_vendor_create_option(
                {"option_name": "X", "bid_amount": 1, "turn_time_days": 1}
            )

    def test_invitation_validation_and_immutability(self):
        order = self._order()
        model = self.env["trucalc.bid.invitation"].with_user(self.admin)
        with self.assertRaises(ValidationError):
            model.create({"order_id": order.id, "vendor_id": self.vendor_a.id})
        order.action_bid_requested()
        with self.assertRaises(ValidationError):
            model.create({"order_id": order.id, "vendor_id": self.reviewer_vendor.id})
        self.vendor_b.active = False
        with self.assertRaises(ValidationError):
            model.create({"order_id": order.id, "vendor_id": self.vendor_b.id})
        invitation = model.create({"order_id": order.id, "vendor_id": self.vendor_a.id})
        with self.assertRaises(Exception):
            model.create({"order_id": order.id, "vendor_id": self.vendor_a.id})
        for values in ({"state": "closed"}, {"round_number": 9},
                       {"vendor_id": self.vendor_b.id}, {"order_id": order.id}):
            with self.assertRaises(AccessError):
                invitation.write(values)
        with self.assertRaises(AccessError):
            invitation.unlink()

    def test_multiple_drafts_edit_remove_and_submission(self):
        invitation = self._invitation()
        first = self._draft(invitation)
        second = self._draft(invitation, option_name="Rush", bid_amount=700, turn_time_days=2)
        self.assertEqual(len(invitation.bid_ids), 2)
        first.with_user(self.vendor_user_a).action_vendor_edit_draft({"notes": "updated"})
        with self.assertRaises(AccessError):
            first.with_user(self.vendor_user_b).action_vendor_edit_draft({"notes": "foreign"})
        with self.assertRaises(AccessError):
            first.with_user(self.vendor_user_a).write({"status": "selected"})
        second.with_user(self.vendor_user_a).action_vendor_remove_draft()
        invitation.with_user(self.vendor_user_a).action_vendor_submit()
        self.assertEqual(first.status, "submitted")
        with self.assertRaises(ValidationError):
            self._draft(invitation)
        with self.assertRaises(ValidationError):
            invitation.with_user(self.vendor_user_a).action_vendor_submit()
        with self.assertRaises(AccessError):
            first.unlink()

    def test_submission_is_atomic_and_deadline(self):
        invitation = self._invitation()
        valid = self._draft(invitation)
        invalid = self._draft(invitation, option_name="Invalid", bid_amount=0)
        with self.assertRaises(ValidationError):
            invitation.with_user(self.vendor_user_a).action_vendor_submit()
        self.assertEqual(valid.status, "draft")
        invalid.with_user(self.vendor_user_a).action_vendor_edit_draft({"bid_amount": 600})
        invitation.with_user(self.vendor_user_a).action_vendor_submit()
        self.assertEqual(set(invitation.bid_ids.mapped("status")), {"submitted"})
        expired = self._invitation(
            vendor=self.vendor_b,
            deadline=fields.Datetime.now() - timedelta(seconds=1),
        )
        with self.assertRaises(ValidationError):
            expired.with_user(self.vendor_user_b).action_vendor_create_option(
                {"option_name": "Late", "bid_amount": 1, "turn_time_days": 1}
            )

    def test_correction_disqualification_and_audit(self):
        invitation = self._invitation()
        bid = self._draft(invitation)
        invitation.with_user(self.vendor_user_a).action_vendor_submit()
        with self.assertRaises(ValidationError):
            bid.with_user(self.admin).action_correct_submitted({"bid_amount": 550}, "")
        with self.assertRaises(AccessError):
            bid.with_user(self.vendor_user_a).action_correct_submitted(
                {"bid_amount": 550}, "vendor attempt"
            )
        bid.with_user(self.ops).action_correct_submitted({"bid_amount": 550}, "Phone correction")
        self.assertEqual((bid.status, bid.bid_amount), ("submitted", 550))
        audit = self.env["trucalc.bid.audit"].search([
            ("action", "=", "bid_corrected"), ("bid_id", "=", bid.id)
        ])
        self.assertEqual(audit.old_values["bid_amount"], 500)
        self.assertEqual(audit.new_values["bid_amount"], 550)
        with self.assertRaises(ValidationError):
            bid.with_user(self.admin).action_disqualify("")
        bid.with_user(self.admin).action_disqualify("Incomplete licensing")
        self.assertEqual(bid.status, "disqualified")

    def test_winner_selection_and_reopen(self):
        order = self._order()
        order.action_bid_requested()
        invitation_a = self._invitation(order=order)
        invitation_b = self._invitation(vendor=self.vendor_b, order=order)
        bid_a = self._draft(invitation_a)
        bid_b = self._draft(invitation_b, user=self.vendor_user_b, bid_amount=625)
        invitation_a.with_user(self.vendor_user_a).action_vendor_submit()
        invitation_b.with_user(self.vendor_user_b).action_vendor_submit()
        bid_a.with_user(self.admin).action_select_bid()
        self.assertEqual(bid_a.status, "selected")
        self.assertEqual(bid_b.status, "not_selected")
        self.assertEqual(order.status, "assigned")
        self.assertEqual(order.assigned_vendor_id, self.vendor_a)
        self.assertEqual(order.vendor_fee, 500)
        self.assertEqual({invitation_a.state, invitation_b.state}, {"closed"})
        old_states = (bid_a.status, bid_b.status, invitation_a.state, invitation_b.state)
        order.with_user(self.ops).action_reopen_bidding()
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 2))
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)
        self.assertEqual(
            (bid_a.status, bid_b.status, invitation_a.state, invitation_b.state), old_states
        )
        new_invitation = self._invitation(vendor=self.vendor_a, order=order)
        self.assertEqual(new_invitation.round_number, 2)

    def test_direct_bypasses_are_denied(self):
        order = self._order()
        for values in ({"bidding_round": 8}, {"assigned_vendor_id": self.vendor_a.id},
                       {"vendor_fee": 1}, {"status": "bid_requested"}):
            with self.assertRaises(AccessError):
                order.write(values)
        order.action_bid_requested()
        with self.assertRaises(AccessError):
            order.write({"status": "assigned"})
        invitation = self._invitation(order=order)
        for status in ("submitted", "selected"):
            with self.assertRaises(AccessError):
                self.env["trucalc.bid"].with_user(self.vendor_user_a).create({
                    "invitation_id": invitation.id, "option_name": "Forged",
                    "bid_amount": 1, "turn_time_days": 1, "status": status,
                })
        bid = self._draft(invitation)
        for values in ({"status": "selected"}, {"invitation_id": invitation.id},
                       {"order_id": order.id}, {"vendor_id": self.vendor_b.id},
                       {"company_id": self.env.company.id}, {"round_number": 9}):
            with self.assertRaises(AccessError):
                bid.with_user(self.vendor_user_a).write(values)
        with self.assertRaises(AccessError):
            bid.with_context(skip_security=True).write({"status": "selected"})

    def test_revoke_and_decline_distinction(self):
        revoked = self._invitation()
        retained = self._draft(revoked)
        revoked.action_revoke()
        self.assertTrue(retained.exists())
        self.assertEqual(revoked.state, "revoked")
        declined = self._invitation(vendor=self.vendor_b)
        removed = self._draft(declined, user=self.vendor_user_b)
        declined.with_user(self.vendor_user_b).action_vendor_decline()
        self.assertFalse(removed.exists())
        self.assertEqual(declined.state, "declined")

    def test_audit_is_immutable(self):
        order = self._order()
        order.action_bid_requested()
        audit = self.env["trucalc.bid.audit"].search([
            ("action", "=", "bidding_started"), ("order_id", "=", order.id)
        ], limit=1)
        self.assertTrue(audit)
        with self.assertRaises(AccessError):
            audit.write({"reason": "tamper"})
        with self.assertRaises(AccessError):
            audit.unlink()
        with self.assertRaises(AccessError):
            self.env["trucalc.bid.audit"].create({
                "action": "bidding_started", "order_id": order.id,
                "actor_id": self.admin.id, "company_id": order.company_id.id,
            })


@tagged("post_install", "-at_install", "trucalc_bid_concurrency")
@unittest.skip(
    "Odoo's in-process test transaction defers independent worker completion "
    "until teardown; run this class in an external two-process harness."
)
class TestBidLifecycleConcurrency(TransactionCase):
    """Genuine two-transaction PostgreSQL row-lock tests."""

    def _cursor(self):
        return db_connect(self.env.cr.dbname).cursor()

    def _create_fixture(self, two_bids=True):
        with self._cursor() as cr:
            env = api.Environment(cr, self.env.ref("base.user_admin").id, {})
            vendor_a = env["trucalc.vendor"].create({
                "name": "4A3 Concurrency A", "vendor_type": "appraiser"
            })
            vendor_b = env["trucalc.vendor"].create({
                "name": "4A3 Concurrency B", "vendor_type": "appraiser"
            })
            order = env["trucalc.order"].create({
                "borrower": "4A3 Concurrency",
                "property_address": "2 Lock Row",
                "company_id": env.company.id,
                "service_type": "evaluation",
            })
            order.action_bid_requested()
            invitation_a = env["trucalc.bid.invitation"].create({
                "order_id": order.id, "vendor_id": vendor_a.id,
            })
            bid_a = invitation_a.action_support_create_option({
                "option_name": "A", "bid_amount": 410, "turn_time_days": 4,
            })
            bids = [bid_a]
            invitation_ids = [invitation_a.id]
            if two_bids:
                invitation_b = env["trucalc.bid.invitation"].create({
                    "order_id": order.id, "vendor_id": vendor_b.id,
                })
                bid_b = invitation_b.action_support_create_option({
                    "option_name": "B", "bid_amount": 420, "turn_time_days": 3,
                })
                bids.append(bid_b)
                invitation_ids.append(invitation_b.id)
            cr.execute(
                "UPDATE trucalc_bid SET status = 'submitted' WHERE id = ANY(%s)",
                ([bid.id for bid in bids],),
            )
            cr.commit()
            return {
                "order": order.id,
                "vendors": [vendor_a.id, vendor_b.id],
                "invitations": invitation_ids,
                "bids": [bid.id for bid in bids],
            }

    def _cleanup_fixture(self, fixture):
        with self._cursor() as cr:
            cr.execute("DELETE FROM trucalc_bid_audit WHERE order_id = %s", (fixture["order"],))
            cr.execute("DELETE FROM trucalc_bid WHERE order_id = %s", (fixture["order"],))
            cr.execute("DELETE FROM trucalc_bid_invitation WHERE order_id = %s", (fixture["order"],))
            cr.execute("DELETE FROM trucalc_order WHERE id = %s", (fixture["order"],))
            cr.execute("DELETE FROM trucalc_vendor WHERE id = ANY(%s)", (fixture["vendors"],))
            cr.commit()

    def _assert_competing_transaction_is_waiting(self, worker, result):
        time.sleep(0.1)
        self.assertTrue(worker.is_alive(), "Competing transaction did not remain blocked")
        self.assertTrue(result.empty(), "Competing transaction completed before lock release")

    def _competing_action(self, result, admin_uid, record_model, record_id, method):
        with self._cursor() as cr:
            cr.execute("SELECT pg_backend_pid()")
            result.put(("pid", cr.fetchone()[0]))
            env = api.Environment(cr, admin_uid, {})
            try:
                getattr(env[record_model].browse(record_id), method)()
                cr.commit()
                result.put(("success", None))
            except Exception as error:  # asserted by the coordinating transaction
                cr.rollback()
                result.put(("error", error))

    def test_concurrent_winner_selection_serializes(self):
        admin_uid = self.env.ref("base.user_admin").id
        fixture = None
        try:
            fixture = self._create_fixture(two_bids=True)
            result = Queue()
            with self._cursor() as cr_a:
                env_a = api.Environment(cr_a, admin_uid, {})
                env_a["trucalc.bid"].browse(fixture["bids"][0]).action_select_bid()
                worker = Thread(
                    target=self._competing_action,
                    args=(result, admin_uid, "trucalc.bid", fixture["bids"][1], "action_select_bid"),
                )
                worker.start()
                result.get(timeout=2)  # worker cursor is open and action is starting
                self._assert_competing_transaction_is_waiting(worker, result)
                cr_a.commit()
            worker.join(timeout=5)
            outcome, error = result.get(timeout=2)
            self.assertEqual(outcome, "error")
            self.assertIsInstance(error, (ValidationError, SerializationFailure))
            with self._cursor() as cr:
                cr.execute(
                    "SELECT count(*) FROM trucalc_bid WHERE order_id=%s AND status='selected'",
                    (fixture["order"],),
                )
                self.assertEqual(cr.fetchone()[0], 1)
                cr.execute(
                    "SELECT assigned_vendor_id, vendor_fee, status FROM trucalc_order WHERE id=%s",
                    (fixture["order"],),
                )
                vendor_id, fee, status = cr.fetchone()
                self.assertEqual((vendor_id, fee, status), (fixture["vendors"][0], 410.0, "assigned"))
        finally:
            if fixture:
                self._cleanup_fixture(fixture)

    def test_concurrent_reopen_serializes(self):
        admin_uid = self.env.ref("base.user_admin").id
        fixture = None
        try:
            fixture = self._create_fixture(two_bids=False)
            with self._cursor() as cr:
                env = api.Environment(cr, admin_uid, {})
                env["trucalc.bid"].browse(fixture["bids"][0]).action_select_bid()
                cr.commit()
            result = Queue()
            with self._cursor() as cr_a:
                env_a = api.Environment(cr_a, admin_uid, {})
                env_a["trucalc.order"].browse(fixture["order"]).action_reopen_bidding()
                worker = Thread(
                    target=self._competing_action,
                    args=(result, admin_uid, "trucalc.order", fixture["order"], "action_reopen_bidding"),
                )
                worker.start()
                result.get(timeout=2)  # worker cursor is open and action is starting
                self._assert_competing_transaction_is_waiting(worker, result)
                cr_a.commit()
            worker.join(timeout=5)
            outcome, error = result.get(timeout=2)
            self.assertEqual(outcome, "error")
            self.assertIsInstance(error, (ValidationError, SerializationFailure))
            with self._cursor() as cr:
                cr.execute(
                    "SELECT bidding_round, status, assigned_vendor_id, vendor_fee "
                    "FROM trucalc_order WHERE id=%s",
                    (fixture["order"],),
                )
                self.assertEqual(cr.fetchone(), (2, "bid_requested", None, 0.0))
        finally:
            if fixture:
                self._cleanup_fixture(fixture)
