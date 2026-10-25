"use client";
import { useState } from "react";
import { ErrorAlert } from "@/components/ui";
import { api } from "@/lib/api";
import { label, money } from "@/lib/format";
import type { Profit } from "@/lib/types";

const FIELDS = ["selling_price", "supplier_cost", "shipping_cost", "advertising_cost", "packaging_cost", "other_costs"] as const;

export default function ProfitCalculator() {
  const [v, setV] = useState<Record<string, string>>({ selling_price: "24.99", supplier_cost: "6.50", shipping_cost: "1.20",
    advertising_cost: "1.00", packaging_cost: "0.30", other_costs: "0", returns_allowance_pct: "3", referral_pct: "15",
    fixed_fee: "0.30", payment_pct: "0", fulfilment_fee: "2.70", tax_pct: "20", target_margin_pct: "25" });
  const [currency, setCurrency] = useState("GBP");
  const [taxIncluded, setTaxIncluded] = useState(true);
  const [result, setResult] = useState<Profit | null>(null);
  const [error, setError] = useState<unknown>(null);
  const pct = (k: string) => String(Number(v[k] || 0) / 100);

  async function calc(e?: React.FormEvent) {
    e?.preventDefault();
    try {
      setResult(await api.post<Profit>("/analytics/profit/calculate", {
        currency_code: currency, ...Object.fromEntries(FIELDS.map((f) => [f, v[f] || "0"])),
        returns_allowance_rate: pct("returns_allowance_pct"), tax_rate: pct("tax_pct"), prices_include_tax: taxIncluded,
        target_margin_pct: v.target_margin_pct || null,
        fees: { referral_rate: pct("referral_pct"), fixed_fee: v.fixed_fee || "0", payment_rate: pct("payment_pct"),
          fulfilment_fee: v.fulfilment_fee || "0" },
      }));
      setError(null);
    } catch (err) { setError(err); }
  }

  const input = (k: string, l: string) => (
    <label className="field" key={k}><span>{l}</span><input inputMode="decimal" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>
  );
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Profit Calculator</h1>
        <p>What-if analysis. For a tracked product, use its Profit tab to save time-stamped cost inputs.</p></div></div>
      <div className="grid grid-2">
        <form className="card stack" onSubmit={calc}>
          <div className="form-grid">
            <label className="field"><span>Currency</span><select value={currency} onChange={(e) => setCurrency(e.target.value)}>{["GBP", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select></label>
            {FIELDS.map((f) => input(f, label(f)))}
            {input("returns_allowance_pct", "Returns allowance %")}
            {input("referral_pct", "Referral fee %")}
            {input("fixed_fee", "Fixed fee per sale")}
            {input("payment_pct", "Payment fee %")}
            {input("fulfilment_fee", "Fulfilment fee")}
            {input("tax_pct", "Tax rate %")}
            {input("target_margin_pct", "Target margin %")}
          </div>
          <label className="row small"><input type="checkbox" checked={taxIncluded} onChange={(e) => setTaxIncluded(e.target.checked)} />
            Listed price includes tax (UK VAT); untick for US sales tax collected on top</label>
          <button className="primary">Calculate</button>
        </form>
        <div className="card">
          <h2>Result</h2>
          <ErrorAlert error={error} />
          {!result && <p className="muted">Enter inputs and calculate.</p>}
          {result && (
            <dl className="kv">
              <dt>Net revenue</dt><dd>{money(result.net_revenue, currency)}</dd>
              <dt>Tax</dt><dd>{money(result.tax_amount, currency)}</dd>
              <dt>Marketplace fees</dt><dd>{money(result.marketplace_fees, currency)}</dd>
              <dt>Payment fees</dt><dd>{money(result.payment_fees, currency)}</dd>
              <dt>Returns allowance</dt><dd>{money(result.returns_allowance, currency)}</dd>
              <dt>Total cost</dt><dd>{money(result.total_cost, currency)}</dd>
              <dt><strong>Profit / unit</strong></dt><dd><strong>{money(result.profit, currency)}</strong></dd>
              <dt>Margin (of net revenue)</dt><dd>{result.margin_pct ?? "—"}%</dd>
              <dt>ROI</dt><dd>{result.roi_pct ?? "—"}%</dd>
              <dt>Break-even price</dt><dd>{money(result.break_even_price, currency)}</dd>
              <dt>Price for target margin</dt><dd>{money(result.target_margin_price, currency)}</dd>
            </dl>
          )}
          {result?.assumptions.map((a) => <p key={a} className="small muted">{a}</p>)}
        </div>
      </div>
    </div>
  );
}
