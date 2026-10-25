"use client";
import Link from "next/link";
import { BarList } from "@/components/charts";
import { Empty, ErrorAlert, JobStatusBadge, Stat } from "@/components/ui";
import { can } from "@/lib/api";
import { ago, money, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Job, Page } from "@/lib/types";

type Overview = {
  total_listings: number;
  by_marketplace: { marketplace: string; name: string; listings: number; avg_price: string | null; currency: string }[];
  stock_status: Record<string, number>;
  source_types: Record<string, number>;
  price_bands: { band: string; currency: string; listings: number }[];
  top_categories: { category: string; listings: number }[];
  changed_last_24h: number;
  stale_over_7_days: number;
  listings_with_metrics: Record<string, number>;
  aggregates_as_of: string | null;
};
type OwnAccount = { accounts: { account_name: string; marketplace: string; currency: string; orders: number; units: number;
  gross_sales: string; refunds: string; marketplace_fees: string | null; fees_complete: boolean }[] };

const BAND_ORDER = ["0-10", "10-25", "25-50", "50-100", "100-250", "250-500", "500+"];

export default function Dashboard() {
  const { data: o, error } = useApi<Overview>("/analytics/overview");
  const { data: jobs } = useApi<Page<Job>>("/jobs?limit=6");
  const { data: own } = useApi<OwnAccount>(can("view_own_account_data") ? "/analytics/own-account" : null);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>{o?.aggregates_as_of ? `Aggregates as of ${new Date(o.aggregates_as_of).toLocaleString()}` : "Live figures"}</p>
        </div>
        <div className="row">
          <Link className="btn" href="/products/">Browse products</Link>
          {can("run_jobs") && <Link className="btn primary" href="/discover/" style={{ color: "white", background: "var(--accent)" }}>New research job</Link>}
        </div>
      </div>
      <ErrorAlert error={error} />
      {o && o.total_listings === 0 && (
        <div className="card">
          <h2>Get started</h2>
          <p className="secondary">Run your first research job: paste a shop, category or product URL, or search by keyword.
            The Demo marketplace returns clearly labelled synthetic data so you can try every feature safely.</p>
          <Link href="/discover/">Start product discovery →</Link>
        </div>
      )}
      {o && (
        <>
          <div className="grid grid-4">
            <Stat label="Listings tracked" value={num(o.total_listings)} />
            <Stat label="Changed in last 24 h" value={num(o.changed_last_24h)} hint="price, stock, rank or rating" />
            <Stat label="Stale (> 7 days)" value={num(o.stale_over_7_days)} hint="due for refresh" />
            <Stat label="Out of stock" value={num(o.stock_status.out_of_stock ?? 0)} />
          </div>
          <div className="grid grid-2">
            <div className="card">
              <h2>Listings by marketplace</h2>
              <BarList rows={o.by_marketplace.map((m) => ({ label: m.name, value: m.listings,
                title: `${m.name}: ${m.listings.toLocaleString()} listings, average ${money(m.avg_price, m.currency)}` }))} />
            </div>
            <div className="card">
              <h2>Top categories</h2>
              <BarList rows={o.top_categories.map((c) => ({ label: c.category, value: c.listings }))} />
            </div>
            {Array.from(new Set(o.price_bands.map((b) => b.currency))).map((cur) => (
              <div className="card" key={cur}>
                <h2>Price bands ({cur})</h2>
                <BarList rows={BAND_ORDER.map((band) => ({ label: band,
                  value: o.price_bands.find((b) => b.band === band && b.currency === cur)?.listings ?? 0 }))} />
              </div>
            ))}
            <div className="card">
              <h2>Data provenance</h2>
              <table>
                <tbody>
                  {Object.entries(o.source_types).map(([k, v]) => (
                    <tr key={k}><td>{k.replace(/_/g, " ")}</td><td className="num">{num(v)}</td></tr>
                  ))}
                  <tr><td colSpan={2} className="small muted">Listings with metrics by measurement type</td></tr>
                  {["actual", "estimated", "unavailable"].map((k) => (
                    <tr key={k}><td><span className={`badge ${k}`}>{k}</span></td><td className="num">{num(o.listings_with_metrics[k] ?? 0)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      {own && own.accounts.length > 0 && (
        <div className="card">
          <h2>Own-account sales (last 30 days) <span className="badge actual">Actual</span></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Account</th><th>Marketplace</th><th className="num">Orders</th><th className="num">Units</th>
                <th className="num">Gross sales</th><th className="num">Refunds</th><th className="num">Fees</th></tr></thead>
              <tbody>
                {own.accounts.map((a) => (
                  <tr key={a.account_name + a.currency}>
                    <td>{a.account_name}</td><td>{a.marketplace}</td><td className="num">{num(a.orders)}</td>
                    <td className="num">{num(a.units)}</td><td className="num">{money(a.gross_sales, a.currency)}</td>
                    <td className="num">{money(a.refunds, a.currency)}</td>
                    <td className="num">{a.marketplace_fees ? money(a.marketplace_fees, a.currency) : "—"}
                      {!a.fees_complete && <span className="badge unavailable" title="Some order lines have no fee data">partial</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="card">
        <div className="row"><h2>Recent jobs</h2><span className="spacer" /><Link href="/jobs/">All jobs →</Link></div>
        {jobs && jobs.items.length === 0 && <Empty>No jobs yet.</Empty>}
        {jobs && jobs.items.length > 0 && (
          <table>
            <tbody>
              {jobs.items.map((j) => (
                <tr key={j.id}>
                  <td><Link href={`/jobs/view/?id=${j.id}`}>{j.search_keyword ?? j.input_url ?? j.job_type.replace("_", " ")}</Link></td>
                  <td>{j.marketplace_code}</td>
                  <td><JobStatusBadge status={j.job_status} /></td>
                  <td className="num">{num(j.products_processed)} / {num(j.products_discovered)}</td>
                  <td className="muted">{ago(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
