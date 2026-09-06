"""Run via odoo-bin shell ONLY on a disposable trucalc_4e2_* database.

Commits normal-workflow fixtures. Requires the real PDF renderer on PATH.
Each contender runs in an independent Odoo process and transaction.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from odoo.addons.trucalc_orders.tests.test_bank_invoice import BankInvoiceFixtures

assert env.cr.dbname.startswith("trucalc_4e2_"), "Disposable 4E.2 database required"


class FixtureBase:
    @classmethod
    def setUpClass(cls):
        pass


class Fixture(BankInvoiceFixtures, FixtureBase):
    pass


Fixture.env = env
Fixture.setUpClass()
fixture = Fixture()

WORKER = r'''
import json
import time
from pathlib import Path
from odoo import api, SUPERUSER_ID, fields
from odoo.exceptions import AccessError, ValidationError
from psycopg2.errors import SerializationFailure
root = Path(args['directory'])
def wait(name):
    deadline = time.monotonic() + 60
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.02)
def operation(local):
    order = local['trucalc.order'].browse(args['oid']).with_user(args['actor'])
    invoice = local['trucalc.bank.invoice'].with_user(args['actor']).search([('order_id', '=', order.id)])
    op = args['operation']
    if op == 'issue':
        return order.action_complete_order()
    if op in ('paid','void') and not invoice:
        raise ValidationError('Invoice is not committed yet')
    if op == 'paid':
        return invoice._mark_paid(fields.Date.today(), 'RACE-CHECK')
    if op == 'void':
        return invoice._void('Concurrency void')
    if op == 'mutation':
        return order.write({'borrower': 'Forbidden concurrent edit'})
    if op == 'fee':
        req = local['trucalc.fee.change.request'].browse(args['rid']).with_user(args['bank_actor'])
        return req._decide(order, 'approved')
    raise AssertionError(op)
result = {'serialization_retries': 0}
if args['role'] == 'winner':
    env.cr.execute("SET LOCAL lock_timeout = '45s'")
    env['trucalc.order'].browse(args['oid'])._lock_for_bid_lifecycle()
    (root / 'locked').touch()
    wait('stale')
else:
    wait('locked')
    env.cr.execute("SET LOCAL lock_timeout = '45s'")
    env['trucalc.bank.invoice'].search_count([('order_id', '=', args['oid'])])
    (root / 'stale').touch()
try:
    operation(env)
    if args['role'] == 'winner':
        time.sleep(.3)
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
        except (AccessError, ValidationError):
            cr.rollback()
            result['outcome'] = 'rejected'
except (AccessError, ValidationError):
    env.cr.rollback()
    result['outcome'] = 'rejected'
(root / (args['role'] + '.json')).write_text(json.dumps(result))
'''


def race(name, first, second, existing=False, pending=False, expected=("ok","rejected")):
    order = fixture._ready_invoice_order(approve_fee=True)
    rid = order.current_fee_change_request_id.id
    if pending:
        rid = order.with_user(fixture.admin).action_request_fee_change(800, "Pending concurrency fee")
    if existing:
        invoice = fixture._issue_invoice(order)
        original_pdf = invoice.pdf_data
    env.flush_all()
    env.cr.execute("SELECT to_jsonb(o) FROM trucalc_order o WHERE id=%s", (order.id,))
    original_order = env.cr.fetchone()[0]
    env.cr.commit()
    directory = Path(tempfile.mkdtemp(prefix="trucalc_4e2_race_"))
    processes = []
    for role, op in (("winner", first), ("contender", second)):
        args = dict(directory=str(directory), oid=order.id, actor=fixture.admin.id,
                    bank_actor=fixture.bank_admin.id, rid=rid, role=role, operation=op)
        command = [sys.executable, sys.argv[0], "shell", "-c",
                   "/Users/Scott/Projects/odoo/debian/odoo.conf", "-d", env.cr.dbname,
                   "--no-http", "--max-cron-threads=0", "--data-dir=/tmp/trucalc_4e2_data",
                   "--logfile=" + str(directory / (role + ".log"))]
        output = (directory / (role + ".out")).open("w")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
        process.stdin.write("args = " + repr(args) + "\n" + WORKER)
        process.stdin.close()
        processes.append((process, output))
    try:
        for process, output in processes:
            assert process.wait(timeout=120) == 0, str(directory)
            output.close()
    finally:
        for process, output in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
            output.close()
    one = json.loads((directory / "winner.json").read_text())
    two = json.loads((directory / "contender.json").read_text())
    assert (one["outcome"],two["outcome"]) == expected, (name, one, two, str(directory))
    env.cr.rollback()
    env.invalidate_all()
    invoice = env["trucalc.bank.invoice"].search([("order_id", "=", order.id)])
    if pending and first == "issue":
        assert not invoice and order.status == "under_review"
        assert not env["trucalc.order.lifecycle.event"].search_count([("order_id","=",order.id),("event_type","in",["order_completed","bank_invoice_issued"])])
        print(json.dumps({"case":name,"winner":one,"contender":two,"status":order.status}),flush=True)
        return
    assert len(invoice) == 1 and invoice.pdf_data
    completion = env["trucalc.order.lifecycle.event"].search([("order_id","=",order.id),("event_type","=","order_completed")])
    assert len(completion)==1 and order.status=="completed" and completion.event_at==invoice.issued_at
    events = env["trucalc.order.lifecycle.event"].search([("bank_invoice_id", "=", invoice.id)])
    assert len(events.filtered(lambda e: e.event_type == "bank_invoice_issued")) == 1
    terminal = events.filtered(lambda e: e.event_type != "bank_invoice_issued")
    assert len(terminal) == (1 if existing else 0)
    assert invoice.status == (first if existing else "issued")
    assert not (invoice.paid_at and invoice.voided_at)
    events._check_bank_invoice_event()
    if existing:
        assert invoice.pdf_data == original_pdf
    env.cr.execute("SELECT to_jsonb(o) FROM trucalc_order o WHERE id=%s", (order.id,))
    final_order=env.cr.fetchone()[0]
    if not existing:
        for field in ("status","write_date","write_uid"):
            final_order.pop(field,None); original_order.pop(field,None)
        if pending:
            for field in ("current_agreed_fee","current_fee_change_request_id","fee_workflow_revision"):
                final_order.pop(field,None); original_order.pop(field,None)
            assert invoice.amount==800
    assert final_order == original_order
    print(json.dumps({"case": name, "winner": one, "contender": two, "invoice": invoice.id,
                      "status": invoice.status, "directory": str(directory)}), flush=True)


race("double_complete_real_pdf", "issue", "issue")
race("double_paid", "paid", "paid", True)
race("double_void", "void", "void", True)
race("paid_vs_void", "paid", "void", True)
race("void_vs_paid", "void", "paid", True)
race("issue_vs_mutation", "issue", "mutation")
race("issue_vs_fee_decision", "issue", "fee")
race("complete_vs_paid_before_commit", "issue", "paid")
race("complete_vs_void_before_commit", "issue", "void")
race("complete_vs_pending_approval", "issue", "fee", pending=True, expected=("rejected","ok"))
race("approval_vs_complete", "fee", "issue", pending=True, expected=("ok","ok"))
print("BANK_INVOICE_CONCURRENCY_PASS", flush=True)
