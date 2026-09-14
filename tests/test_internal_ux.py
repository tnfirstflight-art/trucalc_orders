from ast import literal_eval
from datetime import timedelta
import json
from pathlib import Path

from lxml import etree

from odoo import Command, fields
from odoo.fields import Domain
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from ..models.ir_ui_menu import IrUiMenu


@tagged("post_install", "-at_install", "trucalc_internal_ux")
class TestInternalUXPassA(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.admin = cls._user("admin", ["group_trucalc_admin"])
        cls.ops = cls._user("ops", ["group_trucalc_operations"])
        cls.admin_reviewer = cls._user(
            "admin-reviewer",
            ["group_trucalc_admin", "group_trucalc_reviewer"],
        )
        cls.ops_reviewer = cls._user(
            "ops-reviewer",
            ["group_trucalc_operations", "group_trucalc_reviewer"],
        )

    @classmethod
    def _user(cls, suffix, group_names):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Internal UX %s" % suffix,
            "login": "internal-ux-%s@example.test" % suffix,
            "email": "internal-ux-%s@example.test" % suffix,
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.%s" % name).id
                for name in group_names
            ])],
        })

    def _order(self, label, status="new", due_date=None):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Internal UX %s" % label,
            "property_address": "%s UX Way" % label,
            "company_id": self.company.id,
            "service_type": "evaluation",
            "due_date": due_date or fields.Date.add(fields.Date.today(), days=14),
        })
        if status != "new":
            order._controlled_lifecycle_write({"status": status})
        return order

    def _search_arch(self):
        return etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_search"
        ).arch_db.encode())

    def _filter_domain(self, name):
        node = self._search_arch().xpath(
            "./filter[@name='%s']" % name
        )[0]
        return safe_eval(node.get("domain"), {
            "context_today": fields.Date.today,
        })

    def _launcher_ids(self):
        return {
            label: self.env.ref(xmlid).id
            for label, xmlid in {
                "Discuss": "mail.menu_root_discuss",
                "To-Do": "project_todo.menu_todo_todos",
                "Contacts": "contacts.menu_contacts",
                "Project": "project.menu_main_pm",
                "Apps": "base.menu_management",
                "Settings": "base.menu_administration",
                "TruCalc": "trucalc_orders.menu_trucalc_root",
            }.items()
        }

    def _launcher_user(self, suffix, role=None, reviewer=False):
        group_xmlids = [
            "base.group_system",
            "project.group_project_manager",
        ]
        if role:
            group_xmlids.append(
                "trucalc_orders.group_trucalc_%s" % role
            )
        if reviewer:
            group_xmlids.append("trucalc_orders.group_trucalc_reviewer")
        return self.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX launcher %s" % suffix,
            "login": "internal-ux-launcher-%s@example.test" % suffix,
            "email": "internal-ux-launcher-%s@example.test" % suffix,
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([
                self.env.ref(xmlid).id for xmlid in group_xmlids
            ])],
        })

    def _visible_launcher_labels(self, user):
        launcher_ids = self._launcher_ids()
        visible_ids = self.env["ir.ui.menu"].with_user(
            user
        )._visible_menu_ids(False)
        return {
            label for label, menu_id in launcher_ids.items()
            if menu_id in visible_ids
        }

    def _loaded_launcher_labels(self, user):
        launcher_ids = self._launcher_ids()
        loaded = self.env["ir.ui.menu"].with_user(user).load_menus(False)
        return {
            label for label, menu_id in launcher_ids.items()
            if menu_id in loaded
        }

    def test_navigation_hierarchy_labels_sequences_and_groups(self):
        root = self.env.ref("trucalc_orders.menu_trucalc_root")
        menus = {
            name: self.env.ref("trucalc_orders.%s" % name)
            for name in (
                "menu_trucalc_orders", "menu_unpaid_bank_invoices",
                "menu_trucalc_draft_support", "menu_trucalc_vendors",
                "menu_trucalc_configuration", "menu_trucalc_banks",
                "menu_trucalc_service_areas", "menu_trucalc_negotiated_fees",
                "menu_trucalc_document_tags",
            )
        }
        self.assertEqual(
            [(menu.name, menu.sequence) for menu in root.child_id.sorted(
                lambda menu: (menu.sequence, menu.id)
            )],
            [
                ("Orders", 10), ("Unpaid Invoices", 15),
                ("Bank Draft Support", 20), ("Vendors", 30),
                ("Configuration", 90),
            ],
        )
        self.assertEqual(menus["menu_trucalc_draft_support"].parent_id, root)
        self.assertEqual(
            [(menu.name, menu.sequence) for menu in menus[
                "menu_trucalc_configuration"
            ].child_id.sorted(lambda menu: (menu.sequence, menu.id))],
            [
                ("Banks", 10), ("Service Areas", 20),
                ("Negotiated Fee Schedules", 30), ("Document Tags", 40),
            ],
        )
        self.assertEqual(
            self.env.ref("trucalc_orders.action_trucalc_negotiated_fees").name,
            "Negotiated Fee Schedules",
        )
        admin_group = self.env.ref("trucalc_orders.group_trucalc_admin")
        ops_group = self.env.ref("trucalc_orders.group_trucalc_operations")
        reviewer_group = self.env.ref("trucalc_orders.group_trucalc_reviewer")
        self.assertEqual(
            menus["menu_trucalc_vendors"].group_ids,
            admin_group | ops_group,
        )
        self.assertNotIn(reviewer_group, menus["menu_trucalc_vendors"].group_ids)
        self.assertEqual(
            menus["menu_trucalc_draft_support"].group_ids,
            admin_group,
        )

    def test_role_specific_root_menu_visibility(self):
        menu_ids = {
            name: self.env.ref("trucalc_orders.%s" % name).id
            for name in (
                "menu_trucalc_orders", "menu_unpaid_bank_invoices",
                "menu_trucalc_draft_support", "menu_trucalc_vendors",
                "menu_trucalc_configuration",
            )
        }
        for user in (self.admin, self.admin_reviewer):
            loaded = self.env["ir.ui.menu"].with_user(user).load_menus(False)
            self.assertTrue(all(menu_id in loaded for menu_id in menu_ids.values()))
        for user in (self.ops, self.ops_reviewer):
            loaded = self.env["ir.ui.menu"].with_user(user).load_menus(False)
            for name in ("menu_trucalc_orders", "menu_unpaid_bank_invoices", "menu_trucalc_vendors"):
                self.assertIn(menu_ids[name], loaded)
            for name in ("menu_trucalc_draft_support", "menu_trucalc_configuration"):
                self.assertNotIn(menu_ids[name], loaded)

        reviewer_only = self.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX legacy reviewer-only",
            "login": "internal-ux-legacy-reviewer-only@example.test",
            "email": "internal-ux-legacy-reviewer-only@example.test",
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
            ])],
        })
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (
                reviewer_only.id,
                self.env.ref("trucalc_orders.group_trucalc_reviewer").id,
            ),
        )
        self.env.invalidate_all()
        loaded = self.env["ir.ui.menu"].with_user(reviewer_only).load_menus(False)
        self.assertNotIn(menu_ids["menu_trucalc_vendors"], loaded)
        self.assertNotIn(menu_ids["menu_trucalc_configuration"], loaded)

    def test_app_launcher_visibility_by_trucalc_persona(self):
        admin = self._launcher_user("admin", "admin")
        operations = self._launcher_user("operations", "operations")
        admin_reviewer = self._launcher_user(
            "admin-reviewer", "admin", reviewer=True
        )
        operations_reviewer = self._launcher_user(
            "operations-reviewer", "operations", reviewer=True
        )
        expected_admin = {"TruCalc", "Discuss", "Apps", "Settings"}
        expected_operations = {"TruCalc", "Discuss"}
        self.assertEqual(
            self._visible_launcher_labels(admin), expected_admin
        )
        self.assertEqual(self._loaded_launcher_labels(admin), expected_admin)
        self.assertEqual(
            self._visible_launcher_labels(admin_reviewer), expected_admin
        )
        self.assertEqual(
            self._loaded_launcher_labels(admin_reviewer), expected_admin
        )
        self.assertEqual(
            self._visible_launcher_labels(operations), expected_operations
        )
        self.assertEqual(
            self._loaded_launcher_labels(operations), expected_operations
        )
        self.assertEqual(
            self._visible_launcher_labels(operations_reviewer),
            expected_operations,
        )
        self.assertEqual(
            self._loaded_launcher_labels(operations_reviewer),
            expected_operations,
        )

        non_trucalc = self._launcher_user("non-trucalc")
        menu_model = self.env["ir.ui.menu"].with_user(non_trucalc)
        upstream_ids = super(IrUiMenu, menu_model)._visible_menu_ids(False)
        self.assertEqual(menu_model._visible_menu_ids(False), upstream_ids)
        self.assertEqual(
            self._visible_launcher_labels(non_trucalc),
            {"Discuss", "To-Do", "Contacts", "Project", "Apps", "Settings"},
        )
        self.assertEqual(
            self._loaded_launcher_labels(non_trucalc),
            {"Discuss", "To-Do", "Contacts", "Project", "Apps", "Settings"},
        )

        reviewer_only = self.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX launcher reviewer-only",
            "login": "internal-ux-launcher-reviewer-only@example.test",
            "email": "internal-ux-launcher-reviewer-only@example.test",
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
            ])],
        })
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (
                reviewer_only.id,
                self.env.ref("trucalc_orders.group_trucalc_reviewer").id,
            ),
        )
        self.env.invalidate_all()
        self.assertEqual(
            self._loaded_launcher_labels(reviewer_only), set()
        )

        malformed = self._launcher_user("malformed", "admin")
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (
                malformed.id,
                self.env.ref("trucalc_orders.group_trucalc_operations").id,
            ),
        )
        self.env.invalidate_all()
        self.assertEqual(
            self._visible_launcher_labels(malformed),
            {"TruCalc", "Discuss"},
        )

    def test_launcher_visibility_refreshes_after_internal_role_change(self):
        user = self._launcher_user("role-change", "admin", reviewer=True)
        self.assertEqual(
            self._visible_launcher_labels(user),
            {"TruCalc", "Discuss", "Apps", "Settings"},
        )
        self.assertEqual(
            self._loaded_launcher_labels(user),
            {"TruCalc", "Discuss", "Apps", "Settings"},
        )
        user.write({
            "group_ids": [
                Command.unlink(
                    self.env.ref("trucalc_orders.group_trucalc_admin").id
                ),
                Command.link(
                    self.env.ref("trucalc_orders.group_trucalc_operations").id
                ),
            ],
        })
        self.assertEqual(
            self._visible_launcher_labels(user), {"TruCalc", "Discuss"}
        )
        self.assertEqual(
            self._loaded_launcher_labels(user), {"TruCalc", "Discuss"}
        )

    def test_default_queue_is_recoverable_and_due_date_first(self):
        action = self.env.ref("trucalc_orders.action_trucalc_orders")
        context = literal_eval(action.context)
        self.assertEqual(context["search_default_open_orders"], 1)
        base_domain = literal_eval(action.domain)
        open_domain = self._filter_domain("open_orders")
        expected_open = {
            "new", "bid_requested", "assigned", "engaged",
            "report_received", "reviewer_assigned", "under_review",
        }
        self.assertEqual(set(open_domain[0][2]), expected_open)

        today = fields.Date.today()
        early = self._order("Early", "engaged", today + timedelta(days=1))
        later = self._order("Later", "new", today + timedelta(days=3))
        terminal = [
            self._order("Terminal %s" % status, status, today + timedelta(days=2))
            for status in ("completed", "cancelled", "declined")
        ]
        model = self.env["trucalc.order"].with_user(self.admin)
        queue = model.search(
            Domain.AND([base_domain, open_domain]),
            order="due_date asc, order_number asc",
        )
        self.assertLess(queue.ids.index(early.id), queue.ids.index(later.id))
        self.assertFalse(any(order in queue for order in terminal))
        all_orders = model.search(base_domain)
        self.assertTrue(all(order in all_orders for order in terminal))

        list_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_list"
        ).arch_db.encode())
        self.assertEqual(
            list_arch.get("default_order"),
            "due_date asc, order_number asc",
        )

    def test_list_columns_attention_and_status_badge(self):
        arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_list"
        ).arch_db.encode())
        fields_by_name = {
            node.get("name"): node for node in arch.xpath("./field")
        }
        default_fields = [
            node.get("name") for node in arch.xpath(
                "./field[not(@column_invisible='True') and not(@optional='hide')]"
            )
        ]
        self.assertEqual(default_fields, [
            "order_number", "company_id", "borrower", "property_address",
            "service_type", "due_date", "status",
        ])
        for name in (
            "engagement_action_required_reason", "assigned_vendor_id",
            "requestor_id", "loan_number", "current_agreed_fee",
            "reviewer_user_id",
        ):
            self.assertEqual(fields_by_name[name].get("optional"), "hide")
        self.assertEqual(
            fields_by_name["order_number"].get("widget"),
            "trucalc_order_number_attention",
        )
        self.assertEqual(
            fields_by_name["requestor_id"].get("string"), "Requester"
        )
        self.assertEqual(fields_by_name["due_date"].get("string"), "Due Date")
        self.assertEqual(fields_by_name["status"].get("widget"), "badge")
        for decoration in (
            "decoration-info", "decoration-warning",
            "decoration-success", "decoration-danger",
        ):
            self.assertTrue(fields_by_name["status"].get(decoration))
        self.assertEqual(
            fields_by_name["engagement_action_required_label"].get(
                "column_invisible"
            ),
            "True",
        )
        self.assertFalse(arch.xpath(
            "./field[@name='engagement_action_required_label' "
            "and not(@column_invisible='True')]"
        ))
        self.assertEqual(arch.get("class"), "o_trucalc_order_list")

    def test_internal_home_action_and_backend_brand_contract(self):
        home_action = self.env.ref("trucalc_orders.action_trucalc_orders")
        for user in (
            self.admin, self.ops, self.admin_reviewer, self.ops_reviewer,
        ):
            self.assertEqual(user.action_id.id, home_action.id)
            self.assertTrue(user._trucalc_uses_orders_home_action())

        normal = self.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX normal Odoo user",
            "login": "internal-ux-normal@example.test",
            "email": "internal-ux-normal@example.test",
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })
        self.assertFalse(normal.action_id)
        self.assertFalse(normal._trucalc_uses_orders_home_action())

        self.admin.sudo().with_context(trucalc_home_action_sync=True).write({
            "action_id": False,
        })
        self.env["res.users"]._trucalc_sync_all_home_actions()
        self.assertEqual(self.admin.action_id.id, home_action.id)
        self.assertFalse(normal.action_id)

        root = Path(__file__).resolve().parents[1]
        manifest = literal_eval((root / "__manifest__.py").read_text())
        backend = manifest["assets"]["web.assets_backend"]
        backend_lazy = manifest["assets"]["web.assets_backend_lazy"]
        frontend = manifest["assets"]["web.assets_frontend"]
        brand_asset = (
            "trucalc_orders/static/src/scss/backend_brand_variables.scss"
        )
        brand_directive = ("prepend", brand_asset)
        self.assertIn(brand_directive, backend)
        self.assertIn(brand_directive, backend_lazy)
        for bundle, assets in manifest["assets"].items():
            if bundle not in ("web.assets_backend", "web.assets_backend_lazy"):
                self.assertNotIn(brand_asset, assets)
                self.assertNotIn(brand_directive, assets)
        self.assertNotIn("web._assets_primary_variables", manifest["assets"])
        self.assertNotIn("web._assets_backend_helpers", manifest["assets"])
        for asset in (
            "trucalc_orders/static/src/js/order_number_attention_field.js",
            "trucalc_orders/static/src/xml/order_number_attention_field.xml",
            "trucalc_orders/static/src/scss/internal_backend.scss",
        ):
            self.assertIn(asset, backend)
            self.assertNotIn(asset, frontend)
        variables = (
            root / "static/src/scss/backend_brand_variables.scss"
        ).read_text()
        self.assertEqual(variables.splitlines(), [
            "$o-brand-odoo: #022f5b;",
            "$o-brand-primary: #022f5b;",
        ])
        for forbidden in (
            "$o-success", "$o-info", "$o-warning", "$o-danger",
            "$theme-colors",
        ):
            self.assertNotIn(forbidden, variables)
        styles = (root / "static/src/scss/internal_backend.scss").read_text()
        for required in (
            ".o_trucalc_order_number_cell",
            ".o_trucalc_order_number_value",
            ".o_trucalc_order_attention",
            "$o-brand-primary",
        ):
            self.assertIn(required, styles)
        for forbidden in (
            ".o_trucalc_internal", ".o_trucalc_order_form",
            ".o_action_manager", ":has(", ".o_main_navbar",
            ".o_web_client", ":root", "#022f5b", ".btn-primary",
            ".btn-warning", ".btn-success", ".btn-danger",
        ):
            self.assertNotIn(forbidden, styles)

        all_js = "\n".join(
            path.read_text() for path in (root / "static/src/js").glob("*.js")
        )
        for forbidden in (
            "o_trucalc_internal", "o_action_manager", "classList.add",
            "classList.toggle",
        ):
            self.assertNotIn(forbidden, all_js)

        widget = (
            root / "static/src/xml/order_number_attention_field.xml"
        ).read_text()
        self.assertIn("o_trucalc_order_number_value", widget)
        self.assertIn('t-if="attentionLabel"', widget)
        self.assertIn("o_trucalc_order_attention", widget)

    def test_search_fields_filters_groupings_and_semantics(self):
        arch = self._search_arch()
        self.assertEqual(
            [node.get("name") for node in arch.xpath("./field")],
            [
                "order_number", "borrower", "loan_number", "company_id",
                "requestor_id", "assigned_vendor_id", "service_type",
                "property_address", "city", "state", "zip_code", "status",
            ],
        )
        expected_filters = {
            "intake", "bidding", "assigned_engaged", "report_received",
            "review_queue", "under_review", "completed",
            "fee_change_pending", "overdue", "action_required",
        }
        self.assertTrue(expected_filters.issubset(set(arch.xpath("./filter/@name"))))
        self.assertEqual(
            set(self._filter_domain("review_queue")[0][2]),
            {"report_received", "reviewer_assigned", "under_review"},
        )
        grouping = {
            node.get("name"): literal_eval(node.get("context"))["group_by"]
            for node in arch.xpath("./filter[@context]")
        }
        self.assertEqual(grouping, {
            "group_bank": "company_id",
            "group_status": "status",
            "group_service_type": "service_type",
            "group_vendor": "assigned_vendor_id",
            "group_reviewer": "reviewer_user_id",
            "group_due_month": "due_date:month",
        })

        today = fields.Date.today()
        past = self._order("Past", "new", today - timedelta(days=1))
        due_today = self._order("Today", "engaged", today)
        future = self._order("Future", "under_review", today + timedelta(days=1))
        terminal = [
            self._order("Past %s" % status, status, today - timedelta(days=1))
            for status in ("completed", "cancelled", "declined")
        ]
        missing_due = self.env["trucalc.order"].with_user(self.admin).with_context(
            default_status="draft", trucalc_internal_draft_intake=True,
        ).create({})
        overdue = self.env["trucalc.order"].with_user(self.admin).search(
            self._filter_domain("overdue")
        )
        self.assertIn(past, overdue)
        for order in [due_today, future, missing_due] + terminal:
            self.assertNotIn(order, overdue)


@tagged("post_install", "-at_install", "trucalc_internal_ux_browser")
class TestInternalUXPassABrowser(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "internal-ux-browser"
        cls.admin = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX Browser Admin",
            "login": "internal-ux-browser-admin",
            "email": "internal-ux-browser-admin@example.test",
            "password": cls.password,
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_trucalc_admin").id,
                cls.env.ref("base.group_system").id,
                cls.env.ref("project.group_project_manager").id,
            ])],
        })
        cls.operations = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX Browser Operations",
            "login": "internal-ux-browser-operations",
            "email": "internal-ux-browser-operations@example.test",
            "password": cls.password,
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_trucalc_operations").id,
                cls.env.ref("base.group_system").id,
                cls.env.ref("project.group_project_manager").id,
            ])],
        })
        cls.first = cls._order("Browser First", "2000-01-01")
        cls.second = cls._order("Browser Second", "2000-01-02")
        cls.attention = cls._attention_order()

    @classmethod
    def _order(cls, label, due_date):
        return cls.env["trucalc.order"].with_user(cls.admin).create({
            "borrower": label,
            "property_address": "%s Way" % label,
            "company_id": cls.env.company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.to_date(due_date),
        })

    @classmethod
    def _attention_order(cls):
        vendor = cls.env["trucalc.vendor"].create({
            "name": "Internal UX Browser Vendor",
        })
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": vendor.id,
            "service_type": "evaluation",
            "fee": 100,
        })
        vendor_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal UX Browser Vendor",
            "login": "internal-ux-browser-vendor",
            "email": "internal-ux-browser-vendor@example.test",
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_vendor_portal").id,
            ])],
            "trucalc_vendor_id": vendor.id,
        })
        delivery = fields.Date.add(fields.Date.today(), days=7)
        order = cls.env["trucalc.order"].with_user(cls.admin).create({
            "borrower": "Browser Attention",
            "property_address": "3 Attention Way",
            "company_id": cls.env.company.id,
            "service_type": "evaluation",
            "due_date": delivery,
            "inspection_contact_name": "Browser Contact",
            "inspection_contact_phone": "+1 901 555 0101",
            "inspection_contact_email": "browser-contact@example.test",
        })
        order.with_user(cls.admin).action_accept_request()
        order.with_user(cls.admin).action_request_vendor_bids(
            vendor, fields.Datetime.now() + timedelta(days=2),
        )
        bid = order.invitation_ids.with_user(
            vendor_user
        ).action_vendor_submit_response("standard_terms_accepted")
        bid.with_user(cls.admin)._action_confirm_engagement()
        cls.env.flush_all()
        cls.env["trucalc.vendor.order"].invalidate_model()
        projection = cls.env["trucalc.vendor.order"].with_user(
            vendor_user
        ).search([("order_number", "=", order.order_number)])
        projection.action_vendor_request_delivery_change(
            fields.Date.add(order.vendor_delivery_date, days=1),
            "Browser attention fixture",
        )
        order._controlled_lifecycle_write({
            "due_date": fields.Date.to_date("2000-01-03"),
        })
        return order

    def test_orders_list_responsive_defaults_and_optional_columns(self):
        first_number = json.dumps(self.first.order_number)
        second_number = json.dumps(self.second.order_number)
        attention_number = json.dumps(self.attention.order_number)
        code = """
            (async () => {
                const wait = () => new Promise(resolve => setTimeout(resolve, 500));
                const view = document.querySelector(
                    '.o_list_view.o_trucalc_order_list'
                );
                const table = view && view.querySelector('.o_list_table');
                if (!view || !table) throw Error('Orders list did not load');
                const headerNodes = [...table.querySelectorAll('thead th')];
                const headers = headerNodes.map(node => node.innerText.trim()).filter(Boolean);
                for (const label of [
                    'Order Number', 'Bank', 'Borrower', 'Property Address',
                    'Service Type', 'Due Date', 'Order Status',
                ]) {
                    if (!headers.includes(label)) throw Error(`Missing default column: ${label}`);
                }
                if (headers.includes('Operational Attention')) throw Error('Standalone attention column remains');
                const orderedHeaders = headers.filter(label => [
                    'Order Number', 'Bank', 'Borrower', 'Property Address',
                    'Service Type', 'Due Date', 'Order Status',
                ].includes(label));
                if (orderedHeaders.join('|') !== [
                    'Order Number', 'Bank', 'Borrower', 'Property Address',
                    'Service Type', 'Due Date', 'Order Status',
                ].join('|')) throw Error('Default column order');
                for (const label of [
                    'Requester', 'Loan Number', 'Current Fee', 'Assigned Reviewer',
                ]) {
                    if (headers.includes(label)) throw Error(`Optional column visible by default: ${label}`);
                }
                const facet = document.querySelector('.o_searchview_facet');
                if (!facet || !facet.innerText.includes('Open Orders')) throw Error('Open Orders is not the default queue');
                const rows = [...table.querySelectorAll('tbody tr.o_data_row')];
                if (rows.length < 2) throw Error('Expected operational rows');
                if (!rows[0].innerText.includes(%s) || !rows[1].innerText.includes(%s)) {
                    throw Error('Due-date-first ordering is not visible');
                }
                const attentionRow = rows.find(row => row.innerText.includes(%s));
                const ordinaryRow = rows.find(row => row.innerText.includes(%s));
                if (!attentionRow?.querySelector(
                    '.o_trucalc_order_number_cell .o_trucalc_order_attention'
                )) throw Error('Attention is not beneath Order Number');
                if (ordinaryRow?.querySelector('.o_trucalc_order_attention')) {
                    throw Error('Attention rendered for an ordinary Order');
                }
                const addressIndex = headerNodes.findIndex(
                    node => node.innerText.trim() === 'Property Address'
                );
                const addressCell = attentionRow?.cells[addressIndex];
                if (!addressCell || !addressCell.innerText.includes('3 Attention Way')) {
                    throw Error('Property Address is not readable');
                }
                if (!table.querySelector('.badge')) throw Error('Status badge missing');
                const navbar = document.querySelector('.o_main_navbar');
                if (!navbar || getComputedStyle(navbar).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Backend navbar is not TruCalc blue');
                }
                const primary = document.querySelector('.o_control_panel .btn-primary');
                if (!primary || getComputedStyle(primary).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Backend primary action is not TruCalc blue');
                }
                const orderNumber = table.querySelector('.o_trucalc_order_number_value');
                if (!orderNumber || getComputedStyle(orderNumber).color !== 'rgb(2, 47, 91)') {
                    throw Error('Backend action/link accent is not TruCalc blue');
                }
                const attention = attentionRow.querySelector('.o_trucalc_order_attention');
                if (getComputedStyle(attention).backgroundColor !== 'rgb(220, 53, 69)') {
                    throw Error('Operational Attention lost danger semantics');
                }
                const fixture = document.createElement('div');
                fixture.style.cssText = 'position:fixed;left:-10000px;top:0';
                fixture.innerHTML = `
                    <button class="btn btn-warning">Warning</button>
                    <button class="btn btn-danger">Danger</button>
                    <button class="btn btn-success">Success</button>
                    <input class="form-check-input" type="checkbox" checked>
                `;
                document.body.append(fixture);
                for (const [kind, expected] of [
                    ['warning', 'rgb(255, 172, 0)'],
                    ['danger', 'rgb(220, 53, 69)'],
                    ['success', 'rgb(40, 167, 69)'],
                ]) {
                    if (getComputedStyle(fixture.querySelector(`.btn-${kind}`)).backgroundColor !== expected) {
                        throw Error(`${kind} semantic color changed`);
                    }
                }
                if (getComputedStyle(fixture.querySelector('.form-check-input')).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Selected control accent is not TruCalc blue');
                }
                fixture.remove();
                const toggle = table.querySelector('.o_optional_columns_dropdown_toggle');
                if (!toggle) throw Error('Optional columns control missing');
                toggle.click(); await wait();
                const optionalItems = [...document.querySelectorAll('.o-dropdown-item')]
                    .filter(node => node.offsetParent !== null)
                    .map(node => node.innerText.trim());
                if (!optionalItems.length) throw Error('Optional columns menu did not open');
                for (const label of [
                    'Requester', 'Loan Number', 'Current Fee', 'Assigned Reviewer',
                ]) {
                    if (!optionalItems.includes(label)) throw Error(`Optional column unavailable: ${label}`);
                }
                toggle.click(); await wait();
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
                    throw Error('Page-level horizontal overflow');
                }
                console.log('test successful');
            })();
        """ % (first_number, second_number, attention_number, second_number)
        action_url = "/odoo/action-trucalc_orders.action_trucalc_orders"
        self.browser_size = "1366x768"
        self.browser_js(
            "/odoo", code, login=self.admin.login,
            ready="!!document.querySelector('.o_trucalc_order_list .o_list_table')",
            timeout=90,
        )
        for size in ("1366x768", "1024x768", "768x1024"):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js(
                    action_url, code, login=self.admin.login,
                    ready="!!document.querySelector('.o_list_view .o_list_table')",
                    timeout=90,
                )
        self.browser_size = "1366x768"
        self.browser_js(
            action_url,
            "document.documentElement.style.zoom = '2';" + code,
            login=self.admin.login,
            ready="!!document.querySelector('.o_list_view .o_list_table')",
            timeout=90,
        )

        self.browser_js(
            "/odoo/discuss",
            """
                const navbar = document.querySelector('.o_main_navbar');
                if (!navbar || getComputedStyle(navbar).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Discuss did not inherit the global backend brand');
                }
                console.log('test successful');
            """,
            login=self.admin.login,
            ready="!!document.querySelector('.o_action_manager')",
            timeout=90,
        )

    def test_app_launcher_visibility_by_internal_role(self):
        cases = (
            (
                self.admin,
                {
                    "trucalc_orders.menu_trucalc_root",
                    "mail.menu_root_discuss",
                    "base.menu_management",
                    "base.menu_administration",
                },
            ),
            (
                self.operations,
                {
                    "trucalc_orders.menu_trucalc_root",
                    "mail.menu_root_discuss",
                },
            ),
        )
        for user, expected in cases:
            expected_json = json.dumps(sorted(expected))
            self.browser_size = "1366x768"
            self.browser_js(
                "/odoo",
                """
                    (async () => {
                        const wait = () => new Promise(
                            resolve => setTimeout(resolve, 500)
                        );
                        if (!document.querySelector('.o_trucalc_order_list')) {
                            throw Error('TruCalc Orders landing changed');
                        }
                        const navbar = document.querySelector('.o_main_navbar');
                        if (!navbar || getComputedStyle(navbar).backgroundColor !== 'rgb(2, 47, 91)') {
                            throw Error('Global backend branding changed');
                        }
                        const toggle = document.querySelector(
                            '.o_navbar_apps_menu button.dropdown-toggle'
                        );
                        if (!toggle) throw Error('App launcher toggle missing');
                        toggle.click();
                        await wait();
                        const apps = [...document.querySelectorAll(
                            '.o-dropdown--menu .o_app'
                        )].filter(node => node.offsetParent !== null);
                        if (!apps.length) throw Error('App launcher did not open');
                        if (apps.some(node => !node.innerText.trim())) {
                            throw Error('Blank app launcher tile');
                        }
                        const xmlids = apps.map(
                            node => node.dataset.menuXmlid
                        );
                        if (new Set(xmlids).size !== xmlids.length) {
                            throw Error('Duplicate app launcher tile');
                        }
                        const expected = %s;
                        if (JSON.stringify([...xmlids].sort()) !== JSON.stringify(expected)) {
                            throw Error(`Unexpected launcher apps: ${xmlids.join(', ')}`);
                        }
                        if (!apps.some(node => node.dataset.menuXmlid === 'mail.menu_root_discuss')) {
                            throw Error('Discuss is not reachable');
                        }
                        toggle.click();
                        await wait();
                        if ([...document.querySelectorAll('.o-dropdown--menu .o_app')]
                            .some(node => node.offsetParent !== null)) {
                            throw Error('App launcher did not close');
                        }
                        console.log('test successful');
                    })();
                """ % expected_json,
                login=user.login,
                ready="!!document.querySelector('.o_trucalc_order_list .o_list_table')",
                timeout=90,
            )

    def test_order_form_primary_and_statusbar_brand(self):
        self.browser_size = "1366x768"
        self.browser_js(
            "/odoo/action-trucalc_orders.action_trucalc_orders/%s" % self.first.id,
            """
                const navbar = document.querySelector('.o_main_navbar');
                if (!navbar || getComputedStyle(navbar).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Order form navbar is not TruCalc blue');
                }
                const primary = document.querySelector('.o_form_statusbar .btn-primary:not(.d-none)');
                if (!primary || getComputedStyle(primary).backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Order form primary action is not TruCalc blue');
                }
                const current = document.querySelector('.o_arrow_button_current');
                if (!current) throw Error('Current statusbar state missing');
                if (getComputedStyle(current, '::before').backgroundColor !== 'rgb(2, 47, 91)') {
                    throw Error('Current statusbar border does not derive from TruCalc blue');
                }
                if (getComputedStyle(current).backgroundColor === 'rgb(113, 99, 158)') {
                    throw Error('Current statusbar still uses Odoo purple');
                }
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
                    throw Error('Order form page-level horizontal overflow');
                }
                console.log('test successful');
            """,
            login=self.admin.login,
            ready="!!document.querySelector('.o_form_view .o_arrow_button_current')",
            timeout=90,
        )
