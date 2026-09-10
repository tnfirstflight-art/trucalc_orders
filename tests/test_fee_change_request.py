from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .test_pricing_architecture import TestPricingArchitecture


@tagged('post_install', '-at_install', 'trucalc_fee_change')
class TestFeeChangeRequest(TestPricingArchitecture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank_admin = cls._user('4e1-bank-admin', 'group_bank_admin', bank=cls.bank_a)
        cls.bank_other = cls._user('4e1-bank-other', 'group_bank_admin', bank=cls.bank_b)
        cls.bank_view = cls._user('4e1-bank-view', 'group_bank_view_only', bank=cls.bank_a)

    def _priced(self):
        model = self.env['trucalc.order'].with_user(self.bank_user)
        order = model._create_bank_draft(self._bank_values(), self.bank_user)
        model._send_bank_draft(order, self._bank_values(), self.bank_user)
        order.with_user(self.admin).action_accept_request()
        return order

    def _request(self, order, fee=650, actor=None):
        rid = order.with_user(actor or self.admin).action_request_fee_change(fee, '  Additional scope  ')
        return self.env['trucalc.fee.change.request'].browse(rid)

    def test_fee_sequential_approval_and_decline(self):
        order = self._priced()
        original = (order.agreed_fee, order.fee_source, order.fee_locked_at, self.area.base_fee, order.vendor_fee)
        req = self._request(order)
        self.assertEqual((req.prior_fee, req.reason, order.current_agreed_fee), (500, 'Additional scope', 500))
        self.assertTrue(order._get_current_effective_fee()['has_pending_request'])
        req.with_user(self.bank_admin)._decide(order, 'approved')
        self.assertEqual((order.current_agreed_fee, order.current_fee_change_request_id, order.fee_workflow_revision), (650, req, 2))
        with self.assertRaises(ValidationError):
            req.with_user(self.bank_admin)._decide(order, 'approved')
        second = self._request(order, 725, self.ops)
        self.assertEqual((second.prior_fee, second.prior_approved_request_id), (650, req))
        with self.assertRaises(ValidationError):
            second.with_user(self.bank_admin)._decide(order, 'declined', ' ')
        second.with_user(self.bank_admin)._decide(order, 'declined', ' Budget unchanged ')
        self.assertEqual((order.current_agreed_fee, order.current_fee_change_request_id), (650, req))
        third = self._request(order, 725)
        third.with_user(self.bank_admin)._decide(order, 'approved')
        self.assertEqual((order.current_agreed_fee, third.prior_fee), (725, 650))
        self.assertEqual((order.agreed_fee, order.fee_source, order.fee_locked_at, self.area.base_fee, order.vendor_fee), original)
        events = self.env['trucalc.order.lifecycle.event'].search([('fee_change_request_id', '=', req.id)])
        self.assertEqual(set(events.mapped('event_type')), {'fee_change_requested', 'fee_change_approved'})
        for event in events:
            self.assertEqual((event.order_id, event.company_id, event.from_status, event.to_status), (order, self.bank_a, 'accepted', 'accepted'))
            self.assertEqual(event.actor_id, self.admin if event.event_type.endswith('requested') else self.bank_admin)
            self.assertEqual(event.event_at, req.requested_at if event.event_type.endswith('requested') else req.decision_at)

    def test_fee_validation_eligibility_and_raw_security(self):
        order = self._priced()
        for value in (0, -1, 500, 500.001, float('nan'), float('inf'), 'no', None, True):
            with self.assertRaises(ValidationError):
                self._request(order, value)
        for reason in ('', ' ', 'x' * 5001):
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_request_fee_change(650, reason)
        for status in ('draft', 'new', 'completed', 'declined', 'cancelled'):
            order._controlled_lifecycle_write({'status': status})
            with self.assertRaises(ValidationError):
                self._request(order)
        for status in ('accepted', 'bid_requested', 'assigned', 'engaged', 'report_received', 'reviewer_assigned', 'under_review'):
            order._controlled_lifecycle_write({'status': status})
            req = self._request(order)
            with self.assertRaises(ValidationError):
                self._request(order, 700)
            req.with_user(self.bank_admin)._decide(order, 'declined', 'Not approved')
        for vals in ({'current_agreed_fee': 1}, {'current_fee_change_request_id': req.id}, {'fee_workflow_revision': 99}, {'fee_change_request_ids': [Command.clear()]}):
            with self.assertRaises(AccessError):
                order.sudo().with_context(fee_override=True).write(vals)
            with self.assertRaises(AccessError):
                self.env['trucalc.order'].sudo().create(vals)
        for action in (lambda: req.sudo().write({'state': 'approved'}), lambda: req.sudo().unlink(), lambda: req.sudo().copy(), lambda: req.sudo().create({})):
            with self.assertRaises(AccessError):
                action()

    def test_fee_bank_authorization_and_projection(self):
        order = self._priced()
        req = self._request(order)
        for actor in (self.bank_user, self.bank_view, self.bank_other, self.vendor_user, self.admin):
            with self.assertRaises(AccessError):
                req.with_user(actor)._decide(order, 'approved')
        for actor in (self.bank_user, self.bank_view, self.bank_admin):
            values = req.with_user(actor)._portal_values(order)
            self.assertEqual(values[0]['reason'], 'Additional scope')
            with self.assertRaises(AccessError):
                req.with_user(actor).read(['reason'])
        with self.assertRaises(AccessError):
            req.with_user(self.bank_other)._portal_values(order)
        for actor in (self.bank_admin, self.bank_user, self.vendor_user):
            with self.assertRaises(AccessError):
                self._request(order, actor=actor)
        self.bank_admin.active = False
        with self.assertRaises(AccessError):
            req.with_user(self.bank_admin)._decide(order, 'approved')

    def test_fee_atomicity_and_unpriced(self):
        order = self._priced()
        Event = type(self.env['trucalc.order.lifecycle.event'])
        with patch.object(Event, '_log_fee_change', side_effect=ValidationError('Injected')):
            with self.assertRaises(ValidationError):
                self._request(order)
        self.assertFalse(order._get_current_effective_fee()['has_pending_request'])
        self.assertEqual(order.fee_workflow_revision, 0)
        req = self._request(order)
        with patch.object(Event, '_log_fee_change', side_effect=ValidationError('Injected')):
            with self.assertRaises(ValidationError):
                req.with_user(self.bank_admin)._decide(order, 'approved')
        self.assertEqual((req.state, order.current_agreed_fee, order.fee_workflow_revision), ('pending', 500, 1))
        with self.assertRaisesRegex(ValidationError, 'pending fee'):
            order._completion_valuation()
        order._controlled_lifecycle_write({'current_agreed_fee': 1})
        with self.assertRaises(ValidationError):
            order._get_current_effective_fee()
        order._controlled_lifecycle_write({'fee_locked_at': False})
        with self.assertRaises(ValidationError):
            order._get_current_effective_fee()

    def test_fee_forgery_scope_and_precision(self):
        order = self._priced()
        req = self._request(order, 650.004)
        self.assertEqual(req.proposed_fee, 650)
        other = self._priced()
        with self.assertRaises(AccessError):
            req.with_user(self.bank_admin)._decide(other, 'approved')
        with self.assertRaises(AccessError):
            req.with_user(self.bank_admin)._decide(order, 'pending')
        # A malformed legacy Reviewer-only persona must not gain authority.
        with patch.object(type(self.admin), 'has_group', return_value=False):
            with self.assertRaises(AccessError):
                order.with_user(self.admin).action_request_fee_change(700, 'Forged reviewer')
        with patch.object(type(self.bank_admin), '_trucalc_bank_identity', side_effect=AccessError('Malformed persona')):
            with self.assertRaises(AccessError):
                req.with_user(self.bank_admin)._decide(order, 'approved')
        self.ops.write({'company_ids': [Command.set(self.env.company.ids)]})
        with self.assertRaises(AccessError):
            other.with_user(self.ops).action_request_fee_change(700, 'Foreign company')
        req.with_user(self.bank_admin)._decide(order, 'approved')
        self.area.with_user(self.admin).base_fee = 900
        self._schedule(fee=950)
        self.assertEqual(order._get_current_effective_fee()['amount'], 650)
        with self.assertRaises(AccessError):
            order.copy({'current_agreed_fee': 1})

from odoo import fields
from .test_order_completion import TestOrderCompletion
from .test_bank_order_portal import TestBankOrderPortal


@tagged('post_install', '-at_install', 'trucalc_fee_change')
class TestFeeChangeCompletion(TestOrderCompletion):
    def _price_fixture(self, order):
        area = self.env['trucalc.service.area'].with_user(self.admin).create({
            'state_id': self.env['res.country.state'].search([('code', '=', 'MS'), ('country_id', '=', self.env.ref('base.us').id)], limit=1).id,
            'county': 'Fee completion %s' % order.id, 'service_type': 'evaluation', 'base_fee': 500,
        })
        order._controlled_lifecycle_write({
            'service_area_id': area.id, 'agreed_fee': 500, 'current_agreed_fee': 500,
            'fee_currency_id': area.currency_id.id, 'fee_locked_at': fields.Datetime.now(),
            'fee_source': 'base',
        })
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Fee Decision', 'login': 'fee-completion-bank',
            'group_ids': [Command.set([self.env.ref('trucalc_orders.group_bank_admin').id])],
            'trucalc_bank_company_id': order.company_id.id,
            'company_id': order.company_id.id,
            'company_ids': [Command.set(order.company_id.ids)],
        })

    def test_fee_pending_completion_and_revision(self):
        for decision in ('approved', 'declined'):
            with self.env.cr.savepoint():
                order, valuation, _invoice = self._ready()
                bank = self._price_fixture(order)
                rid = order.with_user(self.admin).action_request_fee_change(650, 'Expanded valuation')
                with self.assertRaisesRegex(ValidationError, 'pending fee'):
                    order.with_user(self.admin).action_complete_order()
                self.env['trucalc.fee.change.request'].browse(rid).with_user(bank)._decide(order, decision, 'Budget declined')
                order.with_user(self.admin).action_complete_order()
                self.assertEqual(order.status, 'completed')
                with self.assertRaises(ValidationError):
                    order.with_user(self.admin).action_request_fee_change(800, 'Too late')
                # Reuse login without mutating unrelated fixture state.
                bank.login = 'fee-completion-bank-' + decision
        order, valuation, _invoice = self._ready(approve=False)
        self._price_fixture(order)
        order.with_user(self.reviewer).action_request_valuation_revision(valuation, 'Please revise')
        rid = order.with_user(self.admin).action_request_fee_change(650, 'Revision adds scope')
        self.assertTrue(rid)
        self.assertEqual(order.status, 'under_review')


@tagged('post_install', '-at_install', 'trucalc_fee_change')
class TestFeeChangePortal(TestBankOrderPortal):
    def test_fee_portal_controls_and_routes(self):
        from lxml import html
        model = self.env['trucalc.order'].with_user(self.bank_requestor)
        values = self._complete_draft_values()
        order = model._create_bank_draft(values, self.bank_requestor)
        model._send_bank_draft(order, values, self.bank_requestor)
        order.with_user(self.admin).action_accept_request()
        rid = order.with_user(self.admin).action_request_fee_change(650, '<script>scope</script>')
        url = '/my/trucalc/bank/orders/%s' % order.order_number
        listing = '/my/trucalc/bank/orders'
        row_xpath = "//tr[@data-order-id='%s']" % order.id
        for actor in (self.bank_requestor, self.bank_viewer, self.bank_admin):
            self._login(actor)
            page = self.url_open(listing)
            self.assertEqual(page.status_code, 200)
            tree = html.fromstring(page.content)
            row = tree.xpath(row_xpath)
            self.assertEqual(len(row), 1)
            headers = row[0].xpath('ancestor::table[1]/thead/tr/th')
            self.assertEqual([h.text_content().strip() for h in headers],
                             ['Order Number', 'Borrower', 'Property Address', 'Service Type', 'Client Due Date', 'Status'])
            self.assertEqual(len(row[0].xpath('./td')), 6)
            status_cell = row[0].xpath('./td')[5]
            self.assertEqual(status_cell.xpath('./div')[0].text_content().strip(), 'Accepted')
            self.assertEqual(status_cell.xpath('./div')[1].get('class'), 'mt-1')
            self.assertEqual(len(status_cell.xpath('./div[2]//button')),
                             int(actor == self.bank_admin))
            if actor != self.bank_admin:
                self.assertEqual(status_cell.xpath('./div[2]')[0].text_content().strip(), 'Pending Fee Change')
            self.assertIn('o_trucalc_fee_pending', row[0].get('class', ''))
            buttons = row[0].xpath(".//button[normalize-space()='Fee Change']")
            self.assertEqual(len(buttons), int(actor == self.bank_admin))
            modal = tree.xpath("//div[@id='fee-change-%s']" % order.id)
            self.assertEqual(len(modal), int(actor == self.bank_admin))
            if actor == self.bank_admin:
                self.assertEqual(buttons[0].get('data-bs-toggle'), 'modal')
                self.assertEqual(buttons[0].get('data-bs-target'), '#fee-change-%s' % order.id)
                for label in ('Current Fee', 'Requested Fee', 'Reason', 'Requested Date', 'Approve', 'Decline', 'Decline Reason'):
                    self.assertIn(label, modal[0].text_content())
                self.assertTrue(modal[0].xpath(".//textarea[@name='decline_reason'][@required][@maxlength='5000']"))
                self.assertEqual(len(modal[0].xpath(".//input[@name='csrf_token']")), 2)
                self.assertIn('&lt;script&gt;scope&lt;/script&gt;', page.text)
            else:
                self.assertFalse(tree.xpath("//form[contains(@action, '/fee/')]"))
                response = self.url_open(url + '/fee/%s/approved' % rid, data={'csrf_token': self._csrf_token()})
                self.assertEqual(response.status_code, 404)
            detail = self.url_open(url + '/documents')
            self.assertEqual(detail.status_code, 200)
            detail_tree = html.fromstring(detail.content)
            history = detail_tree.xpath("//section[@id='fee-changes']")
            self.assertEqual(len(history), 1)
            self.assertEqual(len(history[0].xpath('.//tbody/tr')), 1)
            self.assertFalse(history[0].xpath('.//button|.//form'))
            self.assertFalse(detail_tree.xpath("//form[contains(@action, '/fee/')]"))
            self.assertFalse(history[0].xpath('./following-sibling::*'))
            self.assertIn('&lt;script&gt;scope&lt;/script&gt;', detail.text)
        req = self.env['trucalc.fee.change.request'].browse(rid)
        req.invalidate_recordset()
        self.assertEqual(req.state, 'pending')  # All preceding GETs were read-only.
        self._login(self.other_bank_user)
        foreign = html.fromstring(self.url_open(listing).content)
        self.assertFalse(foreign.xpath(row_xpath))
        self.assertFalse(foreign.xpath("//div[@id='fee-change-%s']" % order.id))
        self.assertEqual(self.url_open(url + '/documents').status_code, 404)
        self.assertEqual(self.url_open(url + '/fee/%s/approved' % rid, data={'csrf_token': self._csrf_token()}).status_code, 404)
        self._login(self.bank_admin)
        target = url + '/fee/%s/approved' % rid
        self.assertIn(self.url_open(target).status_code, (404, 405))
        self.assertEqual(self.url_open(target, data={'csrf_token': 'bad'}).status_code, 400)
        self.assertEqual(self.url_open(target, data={'csrf_token': self._csrf_token(), 'bank_id': self.bank_a.id}).status_code, 404)
        response = self.url_open(target, data={'csrf_token': self._csrf_token()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.url.endswith(listing))
        row = html.fromstring(response.content).xpath(row_xpath)[0]
        self.assertNotIn('o_trucalc_fee_pending', row.get('class', ''))
        self.assertFalse(row.xpath('.//button'))
        self.assertEqual(len(row.xpath('./td')), 6)
        self.assertEqual(row.xpath('./td')[5].text_content().strip(), 'Accepted')
        order.invalidate_recordset()
        self.assertEqual((order.agreed_fee, order.current_agreed_fee), (500, 650))
        rid = order.with_user(self.admin).action_request_fee_change(725, 'More scope')
        target = url + '/fee/%s/declined' % rid
        error_page = self.url_open(target, data={'csrf_token': self._csrf_token(), 'decline_reason': ' '})
        self.assertIn('A reason of 1 to 5000 characters is required.', error_page.text)
        self.assertTrue(html.fromstring(error_page.content).xpath(row_xpath))
        req = self.env['trucalc.fee.change.request'].browse(rid)
        req.invalidate_recordset()
        self.assertEqual(req.state, 'pending')
        response = self.url_open(target, data={'csrf_token': self._csrf_token(), 'decline_reason': 'No budget'})
        self.assertTrue(response.url.endswith(listing))
        req.invalidate_recordset()
        self.assertEqual(req.state, 'declined')
        self.assertNotIn('o_trucalc_fee_pending', html.fromstring(response.content).xpath(row_xpath)[0].get('class', ''))
        order.with_user(self.admin).action_request_fee_change(750, 'Third scope')
        history = html.fromstring(self.url_open(url + '/documents').content).xpath("//section[@id='fee-changes']")[0]
        rows = history.xpath('.//tbody/tr')
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.xpath('./td')[0].text_content().strip() for r in rows], ['Pending', 'Declined', 'Approved'])
        self.assertTrue(all(len(r.xpath('./td')) == 8 for r in rows))
        self.assertIn('No budget', rows[1].text_content())
        self.assertFalse(history.xpath('.//form|.//button|.//dl'))
