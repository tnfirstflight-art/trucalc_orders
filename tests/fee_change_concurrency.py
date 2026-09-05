"""External harness: run through odoo-bin shell ONLY on a disposable 4E.1 DB.

This commits test fixtures. It refuses the persistent development database.
Example: odoo-bin shell -d trucalc_4e1_concurrency --no-http < this_file
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from odoo import Command, fields
from odoo.addons.trucalc_orders.tests.test_review_workflow import PDF

assert env.cr.dbname.startswith('trucalc_4e1_'), 'Disposable 4E.1 database required'
registry = env.registry
company = env['res.company'].create({'name': '4E1 Concurrency Bank'})

def user(name, groups, bank=False, vendor=False):
    return env['res.users'].with_context(no_reset_password=True).create({
        'name': name, 'login': name + '-' + str(company.id),
        'company_id': company.id, 'company_ids': [Command.set(company.ids)],
        'group_ids': [Command.set([env.ref('trucalc_orders.' + group).id for group in groups])],
        'trucalc_bank_company_id': bank.id if bank else False,
        'trucalc_vendor_id': vendor.id if vendor else False,
    })

admin = user('concurrency-admin', ['group_trucalc_admin'])
ops = user('concurrency-ops', ['group_trucalc_operations'])
reviewer = user('concurrency-reviewer', ['group_trucalc_operations', 'group_trucalc_reviewer'])
bank = user('concurrency-bank', ['group_bank_admin'], bank=company)
vendor = env['trucalc.vendor'].create({'name': 'Concurrency Vendor'})
env['trucalc.vendor.fee'].create({'vendor_id': vendor.id, 'service_type': 'evaluation', 'fee': 500})
vendor_user = user('concurrency-vendor', ['group_vendor_portal'], vendor=vendor)
area = env['trucalc.service.area'].with_user(admin).create({
    'state_id': env['res.country.state'].search([('code', '=', 'MS'), ('country_id', '=', env.ref('base.us').id)], limit=1).id,
    'county': 'Concurrency ' + str(company.id), 'service_type': 'evaluation', 'base_fee': 500,
})
admin_id, bank_id = admin.id, bank.id

def setup(pending):
    # The old TransactionCase helper searched projections by order_number. Its
    # internal company had no matching sequence, so committed scenarios reused
    # "New". Use the real Bank intake path and exact authorization identity.
    values = {
        'borrower': 'Concurrency fixture', 'property_address': '41 Fixture Way',
        'city': 'Fixture City', 'zip_code': '38600', 'loan_number': 'CONCURRENCY',
        'service_type': 'evaluation', 'property_type': 'commercial',
        'due_date': fields.Date.to_string(fields.Date.add(fields.Date.today(), days=14)),
        'inspection_contact_name': 'Fixture Contact', 'inspection_contact_phone': '9015550100',
        'inspection_contact_email': 'fixture@example.test', 'notes': '',
        'service_area_id': str(area.id), 'service_state_id': str(area.state_id.id),
        'service_county': area.county,
    }
    model = env['trucalc.order'].with_user(bank)
    order = model._create_bank_draft(values, bank)
    model._send_bank_draft(order, values, bank)
    assert order.order_number != 'New'
    order.with_user(admin).action_accept_request()
    order.with_user(admin).action_request_vendor_bids(vendor, fields.Datetime.now() + timedelta(days=2))
    invitation = order.invitation_ids.filtered(lambda item: item.vendor_id == vendor)
    invitation.ensure_one()
    bid = invitation.with_user(vendor_user).action_vendor_submit_response('standard_terms_accepted')
    bid.with_user(ops)._action_confirm_engagement()
    authorization = order.vendor_authorization_ids.filtered(
        lambda item: item.active and item.source == 'assignment' and item.vendor_id == vendor
    )
    authorization.ensure_one()
    env.flush_all()
    env['trucalc.vendor.order'].invalidate_model()
    projection = env['trucalc.vendor.order'].with_user(vendor_user).search([
        ('id', '=', authorization.id),
    ])
    projection.ensure_one()
    assert projection.order_number == order.order_number
    projection.action_vendor_accept_engagement()
    deliverables = env['trucalc.vendor.deliverable'].with_user(vendor_user)
    valuation = deliverables._submit(authorization, vendor_user, 'valuation', 'Fixture.pdf', PDF)
    deliverables._submit(authorization, vendor_user, 'vendor_invoice', 'Fixture Invoice.pdf', PDF)
    order.with_user(admin).action_assign_reviewer(reviewer)
    order.with_user(reviewer).action_start_review()
    order.with_user(reviewer).action_approve_valuation(valuation)
    rid = order.with_user(admin).action_request_fee_change(650, 'Concurrent scope') if pending else False
    oid = order.id
    assert order._get_current_effective_fee()['amount'] == 500
    env.cr.commit()
    return oid, rid

# Each racer starts its own Odoo interpreter, registry, connection and transaction.
WORKER = r"""
import json
import time
from pathlib import Path
from odoo import api, SUPERUSER_ID
from odoo.exceptions import ValidationError
from psycopg2.errors import SerializationFailure
root = Path(args['directory'])
def wait(name):
    deadline = time.monotonic() + 20
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.02)
def operation(local):
    order = local['trucalc.order'].browse(args['oid'])
    op = args['operation']
    if op == 'request':
        return order.with_user(args['admin']).action_request_fee_change(725, 'Next concurrent scope')
    if op == 'complete':
        return order.with_user(args['admin']).action_complete_order()
    return local['trucalc.fee.change.request'].browse(args['rid']).with_user(args['bank'])._decide(order, op, 'Budget declined')
result = {'serialization_retries': 0}
if args['role'] == 'winner':
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    env['trucalc.order'].browse(args['oid'])._lock_for_bid_lifecycle()
    (root / 'locked').touch()
    wait('stale')
    try:
        operation(env)
        result['outcome'] = 'ok'
    except ValidationError:
        result['outcome'] = 'rejected'
    time.sleep(.3)  # Contender attempts the same Order lock before commit.
    env.cr.commit()
else:
    wait('locked')
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    env['trucalc.fee.change.request'].search_count([('order_id', '=', args['oid'])])
    (root / 'stale').touch()
    try:
        operation(env)
        env.cr.commit()
        result['outcome'] = 'ok'
    except SerializationFailure:
        env.cr.rollback()
        result['serialization_retries'] = 1
        with env.registry.cursor() as cr:
            local = api.Environment(cr, SUPERUSER_ID, {})
            try:
                operation(local)
                cr.commit()
                result['outcome'] = 'ok'
            except ValidationError:
                cr.rollback()
                result['outcome'] = 'rejected'
    except ValidationError:
        env.cr.rollback()
        result['outcome'] = 'rejected'
(root / (args['role'] + '.json')).write_text(json.dumps(result))
"""

def race(name, first, second, pending, expected_first, expected_second):
    oid, rid = setup(pending)
    directory = Path(tempfile.mkdtemp(prefix='trucalc_4e1_race_'))
    processes = []
    for role, operation in [('winner', first), ('contender', second)]:
        args = dict(directory=str(directory), oid=oid, rid=rid, admin=admin_id,
                    bank=bank_id, role=role, operation=operation)
        command = [sys.executable, sys.argv[0], 'shell', '-c',
                   '/Users/Scott/Projects/odoo/debian/odoo.conf', '-d', env.cr.dbname,
                   '--no-http', '--max-cron-threads=0', '--data-dir=/tmp/trucalc_4e1_data',
                   '--logfile=' + str(directory / (role + '.log'))]
        output = (directory / (role + '.out')).open('w')
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
        process.stdin.write('args = ' + repr(args) + '\n' + WORKER)
        process.stdin.close()
        processes.append((process, output))
    try:
        for process, output in processes:
            assert process.wait(timeout=45) == 0, str(directory)
            output.close()
    finally:
        for process, output in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
            output.close()
    one = json.loads((directory / 'winner.json').read_text())
    two = json.loads((directory / 'contender.json').read_text())
    assert one['outcome'] == expected_first, (name, one, str(directory))
    assert two['outcome'] == expected_second, (name, two, str(directory))
    assert two['serialization_retries'] == (1 if expected_first == 'ok' else 0), (name, two)
    with registry.cursor() as cr:
        from odoo import api, SUPERUSER_ID
        local = api.Environment(cr, SUPERUSER_ID, {})
        order = local['trucalc.order'].browse(oid)
        current = order._get_current_effective_fee()
        assert order.agreed_fee == 500
        requests = local['trucalc.fee.change.request'].search([('order_id', '=', oid)])
        assert len(requests.filtered(lambda r: r.state == 'pending')) <= 1
        for item in requests:
            events = local['trucalc.order.lifecycle.event'].search([('fee_change_request_id', '=', item.id)])
            assert len(events.filtered(lambda e: e.event_type == 'fee_change_requested')) == 1
            assert len(events) == (1 if item.state == 'pending' else 2)
            if item.state != 'pending':
                terminal = events.filtered(lambda e: e.event_type != 'fee_change_requested')
                assert terminal.event_type == 'fee_change_' + item.state
                assert terminal.actor_id.id == bank_id and terminal.event_at == item.decision_at
        decisions = requests.filtered(lambda r: r.state != 'pending')
        assert current['fee_workflow_revision'] == len(requests) + len(decisions)
        approved = requests.filtered(lambda r: r.state == 'approved')
        assert current['amount'] == (approved.proposed_fee if approved else 500)
        assert current['current_fee_change_request_id'] == (approved.id if approved else False)
        if order.status == 'completed':
            assert not current['has_pending_request']
        if first == 'approved' and second == 'request':
            latest = requests.filtered(lambda r: r.state == 'pending')
            assert latest.prior_fee == 650 and latest.prior_approved_request_id.id == rid
    print('PASS:', name, one, two, str(directory), flush=True)

cases = [
    ('two new requests / stale snapshot', 'request', 'request', False, 'ok', 'rejected'),
    ('approve vs approve', 'approved', 'approved', True, 'ok', 'rejected'),
    ('approve vs decline', 'approved', 'declined', True, 'ok', 'rejected'),
    ('approval then new request / immediate sequential creation', 'approved', 'request', True, 'ok', 'ok'),
    ('request creation vs completion', 'request', 'complete', False, 'ok', 'rejected'),
    ('approval vs completion', 'approved', 'complete', True, 'ok', 'ok'),
    ('completion vs request creation', 'complete', 'request', False, 'ok', 'rejected'),
    ('decline vs approve', 'declined', 'approved', True, 'ok', 'rejected'),
    ('new request rejected before approval', 'request', 'approved', True, 'rejected', 'ok'),
    ('completion blocked before approval', 'complete', 'approved', True, 'rejected', 'ok'),
]
for case in cases:
    race(*case)
print('PASS: all 10 independent-process scenarios; no deadlocks or partial states', flush=True)
