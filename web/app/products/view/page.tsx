"use client";
import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { LineChart } from "@/components/charts";
import { Empty, ErrorAlert, MeasurementBadge, RemoteImage, SourceBadge, StockBadge, Tabs } from "@/components/ui";
import { api, can } from "@/lib/api";
import { ago, label, money, num } from "@/lib/format";
import { useApi, useQueryParam } from "@/lib/hooks";
import type { ListingDetail, Match, Metric, Observation, Opportunity, Profit } from "@/lib/types";

const TABS = ["Overview", "Images", "Variations", "Specifications", "Price History", "Metrics", "Source Data", "Profit",
  "Matches"] as const;
type Tab = (typeof TABS)[number];

export default function ProductView() {
  const id = useQueryParam("id");
  const [tab, setTab] = useState<Tab>("Overview");
  const { data: p, error, reload } = useApi<ListingDetail>(id ? `/products/${id}` : null);
  if (error) return <ErrorAlert error={error} />;
  if (!p) return <div className="muted">Loading…</div>;
  return (
    <div className="stack">
      <div className="page-head">
        <div style={{ display: "flex", gap: 16 }}>
          {p.main_image_url && <div className="thumb" style={{ width: 88, height: 88 }}><RemoteImage src={p.main_image_url} style={{ width: 88, height: 88, objectFit: "cover", borderRadius: 8 }} /></div>}
          <div>
            <h1>{p.title}</h1>
            <div className="row small">
              <span>{p.marketplace_code}</span><SourceBadge source={p.source_type} /><StockBadge status={p.stock_status} />
              <span className="muted">Updated {ago(p.last_seen_at)}</span>
              <a href={p.product_url} target="_blank" rel="noopener noreferrer">View on marketplace ↗</a>
            </div>
          </div>
        </div>
        <div className="row">
          <div className="stat right"><span className="value">{money(p.current_price, p.currency_code)}</span>
            <span className="label">{p.shipping_price ? `+ ${money(p.shipping_price, p.currency_code)} shipping` : "price as listed"}</span></div>
          {can("run_jobs") && <button onClick={() => api.patch(`/products/${p.id}`, { is_watched: !p.is_watched }).then(reload)}>
            {p.is_watched ? "Unwatch" : "Watch"}</button>}
        </div>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {tab === "Overview" && <Overview p={p} />}
      {tab === "Images" && (p.images.length ? <div className="gallery">{p.images.map((i) =>
        <a key={i.id} href={i.image_url} target="_blank" rel="noopener noreferrer"><RemoteImage src={i.image_url} alt={i.image_type ?? ""} /></a>)}</div>
        : <Empty>No images recorded.</Empty>)}
      {tab === "Variations" && <Variations p={p} />}
      {tab === "Specifications" && <Specs p={p} />}
      {tab === "Price History" && <History id={p.id} currency={p.currency_code} />}
      {tab === "Metrics" && <Metrics id={p.id} />}
      {tab === "Source Data" && <Sources id={p.id} />}
      {tab === "Profit" && <ProfitTab p={p} />}
      {tab === "Matches" && <Matches id={p.id} />}
    </div>
  );
}

function Overview({ p }: { p: ListingDetail }) {
  const { data: opp } = useApi<Opportunity>(`/products/${p.id}/opportunity`);
  return (
    <div className="grid grid-2">
      <div className="card">
        <h2>Key facts</h2>
        <dl className="kv">
          <dt>External ID</dt><dd className="mono">{p.external_listing_id}</dd>
          <dt>Brand</dt><dd>{p.brand ?? "—"}</dd>
          <dt>Category</dt><dd>{p.category_path ?? "—"}</dd>
          <dt>Seller</dt><dd>{p.seller_name ?? "—"}</dd>
          <dt>Sales rank</dt><dd>{p.sales_rank ? `${num(p.sales_rank)} in ${p.rank_category ?? "category"}` : "Not available"}</dd>
          <dt>Rating</dt><dd>{p.rating ? `${p.rating} (${num(p.review_count)} reviews)` : "Not available"}</dd>
          <dt>Stock quantity</dt><dd>{p.stock_quantity !== null ? <>{num(p.stock_quantity)} <MeasurementBadge type={p.stock_quantity_type} /></> : <MeasurementBadge type="unavailable" />}</dd>
          <dt>First seen</dt><dd>{new Date(p.first_seen_at).toLocaleString()}</dd>
          <dt>Canonical product</dt><dd>{p.canonical_product_id ? <Link href={`/products/?canonical_product_id=${p.canonical_product_id}`}>#{p.canonical_product_id}</Link> : "Not linked"}</dd>
        </dl>
        {p.description && <><h3 style={{ marginTop: 16 }}>Description</h3><p className="secondary" style={{ whiteSpace: "pre-wrap" }}>{p.description}</p></>}
      </div>
      <div className="card">
        <h2>Opportunity score</h2>
        {opp && (
          <>
            <div className="row"><span style={{ fontSize: 32, fontWeight: 700 }}>{opp.score.toFixed(0)}</span><span className="muted">/ 100</span>
              <span className={`badge ${opp.confidence >= 0.7 ? "good" : opp.confidence >= 0.4 ? "warn" : "bad"}`}>confidence {(opp.confidence * 100).toFixed(0)}%</span></div>
            {opp.missing.length > 0 && <p className="small muted">Missing data lowers the score: {opp.missing.map(label).join(", ")}.</p>}
            <table>
              <tbody>
                {opp.components.map((c) => (
                  <tr key={c.name}>
                    <td>{label(c.name)}</td>
                    <td className="num">{c.score === null ? <span className="badge unavailable">n/a</span> : c.score.toFixed(0)}</td>
                    <td className="small muted">{c.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

function Variations({ p }: { p: ListingDetail }) {
  if (!p.variations.length) return <Empty>This listing has no variations recorded.</Empty>;
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Variation ID</th><th>SKU</th><th>Size</th><th>Colour</th><th className="num">Price</th><th>Availability</th></tr></thead>
      <tbody>{p.variations.map((v) => (
        <tr key={v.id}><td className="mono">{v.external_variation_id}</td><td className="mono">{v.variation_sku ?? "—"}</td>
          <td>{v.size ?? "—"}</td><td>{v.colour ?? "—"}</td><td className="num">{money(v.price, v.currency_code)}</td>
          <td><StockBadge status={v.stock_status} /></td></tr>))}</tbody>
    </table></div>
  );
}

function Specs({ p }: { p: ListingDetail }) {
  const rows: [string, unknown][] = [["ASIN", p.asin], ["GTIN", p.gtin], ["MPN / model", p.mpn], ["Condition", p.condition],
    ["Fulfilment", p.fulfilment_type], ["Parent listing", p.parent_listing_id], ...Object.entries(p.attributes ?? {})];
  return (
    <div className="card"><dl className="kv">
      {rows.map(([k, v]) => <Fragment key={k}><dt>{label(k)}</dt><dd>{v === null || v === undefined || v === "" ? "—" : String(v)}</dd></Fragment>)}
    </dl></div>
  );
}

function History({ id, currency }: { id: number; currency: string | null }) {
  const [days, setDays] = useState(90);
  const { data } = useApi<Observation[]>(`/products/${id}/history?days=${days}`, [days]);
  const prices = (data ?? []).filter((o) => o.price !== null).map((o) => ({ t: new Date(o.observed_at).getTime(), v: Number(o.price) }));
  const ranks = (data ?? []).filter((o) => o.sales_rank !== null).map((o) => ({ t: new Date(o.observed_at).getTime(), v: Number(o.sales_rank) }));
  return (
    <div className="stack">
      <div className="row">
        {[30, 90, 365].map((d) => <button key={d} className={d === days ? "primary" : ""} onClick={() => setDays(d)}>{d} days</button>)}
        <span className="small muted">{data?.length ?? 0} observations. A point is recorded when a value changes, and at least daily while tracked.</span>
      </div>
      <div className="card"><h2>Price ({currency})</h2>
        <LineChart points={prices} stepped format={(v) => money(v, currency)} ariaLabel="Price history" /></div>
      {ranks.length > 0 && <div className="card"><h2>Sales rank <span className="small muted">(lower is better)</span></h2>
        <LineChart points={ranks} stepped invert format={(v) => num(v)} ariaLabel="Sales rank history" /></div>}
      <details className="card"><summary>Table view</summary>
        <table><thead><tr><th>Observed</th><th className="num">Price</th><th>Availability</th><th className="num">Rank</th><th className="num">Reviews</th></tr></thead>
          <tbody>{[...(data ?? [])].reverse().map((o) => (
            <tr key={o.observed_at}><td>{new Date(o.observed_at).toLocaleString()}</td><td className="num">{money(o.price, o.currency_code)}</td>
              <td><StockBadge status={o.stock_status} /></td><td className="num">{num(o.sales_rank)}</td><td className="num">{num(o.review_count)}</td></tr>))}</tbody>
        </table>
      </details>
    </div>
  );
}

function Metrics({ id }: { id: number }) {
  const { data } = useApi<Metric[]>(`/products/${id}/metrics`);
  if (data && !data.length) return <Empty>No metrics recorded.</Empty>;
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Metric</th><th className="num">Value</th><th>Type</th><th>Methodology</th><th className="num">Confidence</th><th>Source</th><th>Observed</th></tr></thead>
      <tbody>{data?.map((m) => (
        <tr key={m.metric_name}><td>{label(m.metric_name)}</td>
          <td className="num">{m.numeric_value === null ? "Not available" : `${num(m.numeric_value, 2)} ${m.unit ?? ""}`}</td>
          <td><MeasurementBadge type={m.measurement_type} /></td><td className="small">{m.methodology ?? "—"}</td>
          <td className="num">{m.confidence ? `${(Number(m.confidence) * 100).toFixed(0)}%` : "—"}</td>
          <td className="mono">{m.source}</td><td className="small muted">{ago(m.observed_at)}</td></tr>))}</tbody>
    </table></div>
  );
}

function Sources({ id }: { id: number }) {
  const { data } = useApi<{ field_name: string; source_type: string; connector: string; connector_version: string | null;
    endpoint: string | null; source_reference: string | null; retrieved_at: string }[]>(`/products/${id}/sources`);
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Field</th><th>Source type</th><th>Connector</th><th>Endpoint</th><th>Reference</th><th>Retrieved</th></tr></thead>
      <tbody>{data?.map((s) => (
        <tr key={s.field_name}><td>{label(s.field_name)}</td><td><SourceBadge source={s.source_type} /></td>
          <td>{s.connector} {s.connector_version}</td><td className="mono">{s.endpoint ?? "—"}</td>
          <td className="mono small">{s.source_reference ?? "—"}</td><td className="small">{new Date(s.retrieved_at).toLocaleString()}</td></tr>))}</tbody>
    </table></div>
  );
}

function ProfitTab({ p }: { p: ListingDetail }) {
  const [price, setPrice] = useState("");
  const [target, setTarget] = useState("25");
  const { data: r, reload, error } = useApi<Profit>(`/products/${p.id}/profit?target_margin_pct=${target || 0}${price ? `&selling_price=${price}` : ""}`, [price, target]);
  const [cost, setCost] = useState({ currency_code: p.currency_code ?? "GBP", supplier_cost: "", shipping_cost: "0", advertising_cost: "0",
    packaging_cost: "0", other_costs: "0", returns_allowance_rate: "0" });
  const [saveError, setSaveError] = useState<unknown>(null);
  useEffect(() => setSaveError(null), [cost]);
  const c = r?.currency_code;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/products/${p.id}/costs`, cost);
      reload();
    } catch (err) { setSaveError(err); }
  }
  return (
    <div className="grid grid-2">
      <div className="card">
        <h2>Estimated profit per unit</h2>
        <ErrorAlert error={error} />
        {r && (
          <>
            {!r.is_complete && <div className="alert warn small">Profit, margin and break-even need: {r.missing_inputs.map(label).join(", ")}. Unknown costs are never assumed to be zero.</div>}
            <dl className="kv">
              <dt>Selling price</dt><dd>{money(r.selling_price, c)}</dd>
              <dt>Tax removed</dt><dd>{money(r.tax_amount, c)}</dd>
              <dt>Net revenue</dt><dd>{money(r.net_revenue, c)}</dd>
              <dt>Marketplace fees</dt><dd>{money(r.marketplace_fees, c)}</dd>
              <dt>Payment fees</dt><dd>{money(r.payment_fees, c)}</dd>
              <dt>Returns allowance</dt><dd>{money(r.returns_allowance, c)}</dd>
              <dt>Total cost</dt><dd>{money(r.total_cost, c)}</dd>
              <dt><strong>Profit</strong></dt><dd><strong>{money(r.profit, c)}</strong></dd>
              <dt>Margin</dt><dd>{r.margin_pct ? `${r.margin_pct}%` : "—"}</dd>
              <dt>ROI</dt><dd>{r.roi_pct ? `${r.roi_pct}%` : "—"}</dd>
              <dt>Break-even price</dt><dd>{money(r.break_even_price, c)}</dd>
              <dt>Price for {target}% margin</dt><dd>{money(r.target_margin_price, c)}</dd>
            </dl>
            {r.assumptions.map((a) => <p key={a} className="small muted">{a}</p>)}
            {r.fx.map((f) => <p key={f.from} className="small muted">FX {f.from}→{f.to} at {f.rate} ({f.source})</p>)}
          </>
        )}
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field"><span>What-if selling price</span><input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={p.current_price ?? ""} /></label>
          <label className="field"><span>Target margin %</span><input inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} /></label>
        </div>
      </div>
      {can("edit_costs") && (
        <form className="card stack" onSubmit={save}>
          <h2>Add cost inputs</h2>
          <p className="small muted">Costs are time-stamped and never overwritten, so earlier calculations stay reproducible.</p>
          <ErrorAlert error={saveError} />
          <div className="form-grid">
            {(Object.keys(cost) as (keyof typeof cost)[]).map((k) => (
              <label key={k} className="field"><span>{label(k)}</span>
                <input required={k === "supplier_cost"} value={cost[k]} onChange={(e) => setCost({ ...cost, [k]: e.target.value })} /></label>
            ))}
          </div>
          <button className="primary">Save costs</button>
          <p className="small muted">Marketplace fees come from the fee profile for this marketplace (Settings → Fees).</p>
        </form>
      )}
    </div>
  );
}

function Matches({ id }: { id: number }) {
  const [items, setItems] = useState<Match[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = (suggest = false) =>
    (suggest ? api.post<Match[]>(`/products/${id}/matches/suggest`) : api.get<{ items: Match[] }>(`/matches?listing_id=${id}&status=candidate`).then((r) => r.items))
      .then(setItems).catch(setError);
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="stack">
      <div className="row">
        {can("run_jobs") && <button onClick={() => load(true)}>Find candidates across marketplaces</button>}
        <span className="small muted">Exact GTIN first, then brand + model, then title/attribute similarity. Nothing is merged until you confirm.</span>
      </div>
      <ErrorAlert error={error} />
      {items && !items.length && <Empty>No candidates.</Empty>}
      {items?.map((m) => {
        const other = m.source_listing_id === id ? m.target : m.source;
        return (
          <div className="card row" key={m.id}>
            <span className="badge info">{label(m.match_method)} · {(Number(m.score) * 100).toFixed(0)}%</span>
            <Link href={`/products/view/?id=${other.id}`}>{other.title}</Link>
            <span className="muted">{other.marketplace_code} · {money(other.current_price, other.currency_code)}</span>
            <span className="spacer" />
            <span className={`badge ${m.status === "confirmed" ? "good" : m.status === "rejected" ? "bad" : "neutral"}`}>{m.status}</span>
            {can("review_matches") && m.status === "candidate" && <>
              <button onClick={() => api.post(`/matches/${m.id}/review`, { decision: "confirmed" }).then(() => load())}>Confirm</button>
              <button onClick={() => api.post(`/matches/${m.id}/review`, { decision: "rejected" }).then(() => load())}>Reject</button></>}
          </div>
        );
      })}
    </div>
  );
}
