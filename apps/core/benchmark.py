"""The problem statement's section 5 benchmark, replayable on a freshly seeded database.

`run_full_benchmark()` performs steps 1-8 through the same service layer the screens use.
`check_results()` evaluates the "Results we will check" table against the live data.
Used by the `run_benchmark` command and by the end-to-end test.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal as D

from apps.accounts.models import User
from apps.billing import services as billing
from apps.costing import services as costing
from apps.inventory import services as stock
from apps.masters.models import CompanyProfile, Customer, Item, Sku, Unit
from apps.production import services as prod
from apps.production import variance as var
from apps.purchasing.models import Purchase, Supplier, post_purchase
from apps.sales import services as sales


def run_full_benchmark() -> dict:
    u = lambda name: User.objects.get(username=name)  # noqa: E731
    item = lambda code: Item.objects.get(code=code)  # noqa: E731
    fg, cust = Sku.objects.get(code="MUR200"), lambda n: Customer.objects.get(name=n)  # noqa: E731
    production, admin = u("production"), u("admin")
    # 1. purchase 60 kg flour at 45
    post_purchase(Purchase(supplier=Supplier.objects.get(), bill_no="BENCH-1"),
                  [dict(item=item("FLOUR"), qty=D("60"), unit=Unit.objects.get(code="kg"), rate=D("45"))], production)
    # 2. one run; issue 40 kg flour, 10.5 L oil, 1.2 kg spice
    run = prod.start_run(fg, 1, production)
    for code, qty in (("FLOUR", "40"), ("OIL", "10.5"), ("SPICE", "1.2")):
        prod.issue_material(run, item(code), D(qty), production)
    # 3. actual output 188 good, 6 pouches damaged, 9 cartons used
    prod.complete_run(run, 188, 6, {item("CARTON20"): D("9")}, production)
    fg_after_run = stock.balance(fg)  # measured now, before any dispatch
    # 4. Salesman A, Retailer A, 150 packets
    order_a = sales.create_order(u("salesman_a"), cust("Retailer A"), {fg: 150}, client_uuid=uuid.uuid4())
    # 5. admin approves, raises GST invoice, records dispatch
    sales.approve_order(order_a, admin)
    inv_a = billing.raise_invoice(order_a, admin)
    billing.dispatch_invoice(inv_a, admin)
    # 6. Salesman B, Distributor B (Karnataka), 120 packets: insufficient stock
    order_b = sales.create_order(u("salesman_b"), cust("Distributor B"), {fg: 120}, client_uuid=uuid.uuid4())
    # 7. payment of 4000 from Retailer A
    billing.record_payment(cust("Retailer A"), "4000", admin, invoice=inv_a, mode="upi", reference="BENCH-UTR")
    # 8. labour 1200 and gas/power 600 for the run
    costing.add_overhead(production, "labour", "1200", run=run)
    costing.add_overhead(production, "power_gas", "600", run=run)
    run.refresh_from_db()
    return dict(run=run, order_a=order_a, inv_a=inv_a, order_b=order_b, fg_after_run=fg_after_run)


@dataclass
class Check:
    name: str
    expected: str
    actual: str

    @property
    def ok(self):
        return self.expected == self.actual


def _n(x) -> str:
    return format(D(x).normalize(), "f")


def check_results(ctx: dict) -> list[Check]:
    item = lambda code: Item.objects.get(code=code)  # noqa: E731
    fg = Sku.objects.get(code="MUR200")
    rep = prod.run_report(ctx["run"])
    oil = next(l for l in rep.lines if l.item.code == "OIL").variance
    y = rep.yield_variance
    inv = ctx["inv_a"]
    cov = sales.coverage(ctx["order_b"])[0]
    rc = costing.run_cost(ctx["run"])
    return [
        Check("Flour closing balance (kg)", "120", _n(stock.balance(item("FLOUR")))),
        Check("Oil closing balance (L)", "19.5", _n(stock.balance(item("OIL")))),
        Check("Pouches closing balance", "306", _n(stock.balance(item("POUCH200")))),
        Check("Finished goods after run", "238", _n(ctx["fg_after_run"])),
        Check("Finished goods after Retailer A dispatch", "88", _n(stock.balance(fg))),
        Check("Yield variance", "188 vs 200, -6.00%, flagged", f"{y.actual} vs {_n(y.planned)}, {y.pct}%, {'flagged' if y.status == var.FLAG else y.status}"),
        Check("Oil consumption variance", "+5.00%, flagged", f"+{oil.pct}%, {'flagged' if oil.status == var.FLAG else oil.status}"),
        Check("Retailer A invoice", "7500.00 + 187.50 CGST + 187.50 SGST = 7875.00",
              f"{inv.taxable_total} + {inv.cgst_total} CGST + {inv.sgst_total} SGST = {inv.grand_total}"),
        Check("Retailer A outstanding", "3875.00", str(billing.customer_balance(inv.customer))),
        Check("Distributor B shortage", "32 packets flagged, IGST applies", f"{cov.shortage} packets {'flagged' if cov.shortage else 'ok'}, "
              f"{'CGST+SGST' if ctx['order_b'].customer.state_code == CompanyProfile.get().state_code else 'IGST applies'}"),
        Check("Cost per packet (our figure, see README)", "31.14", f"{rc.per_packet.quantize(D('0.01'))}"),
    ]
