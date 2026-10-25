"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Empty, ErrorAlert, MeasurementBadge, StockBadge } from "@/components/ui";
import { api, can } from "@/lib/api";
import { label, money, num } from "@/lib/format";
import type { Listing, Match, Page } from "@/lib/types";

type CompareItem = Listing & {
  converted_price: string | null; fx?: { rate: string; source: string };
  profit: { profit: string | null; margin_pct: string | null; is_complete: boolean; missing_inputs: string[] };
  metrics: { name: string; value: string | null; type: string; unit: string | null }[];
};

export default function Compare() {
  const [ids, setIds] = useState<number[]>([]);
  const [currency, setCurrency] = useState("GBP");
  const [items, setItems] = useState<CompareItem[] | null>(null);
  const [matches, setMatches] = useState<Page<Match> | null>(null);
  const [minScore, setMinScore] = useState("0.6");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("ids");
    if (raw) setIds(raw.split(",").map(Number).filter(Boolean));
  }, []);
  useEffect(() => {
    if (ids.length < 2) { setItems(null); return; }
    api.post<{ items: CompareItem[] }>("/products/compare", { listing_ids: ids, currency }).then((r) => setItems(r.items)).catch(setError);
  }, [ids, currency]);
  const loadMatches = () => api.get<Page<Match>>(`/matches?status=candidate&min_score=${minScore}&limit=50`).then(setMatches).catch(setError);
  useEffect(() => { loadMatches(); }, [minScore]); // eslint-disable-line react-hooks/exhaustive-deps

  async function review(id: number, decision: string) {
    await api.post(`/matches/${id}/review`, { decision }).catch(setError);
    loadMatches();
  }

  const metricNames = Array.from(new Set(items?.flatMap((i) => i.metrics.map((m) => m.name)) ?? []));
  return (
    <div className="stack">
      <div className="page-head">
        <div><h1>Product Comparison</h1><p>Compare listings across marketplaces and review match candidates.</p></div>
        <label className="field"><span>Convert prices to</span>
          <select value={currency} onChange={(e) => setCurrency(e.target.value)}>{["GBP", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select>
        </label>
      </div>
      <ErrorAlert error={error} />
      {items ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Field</th>{items.map((i) => <th key={i.id}><Link href={`/products/view/?id=${i.id}`}>{i.marketplace_code}</Link></th>)}</tr></thead>
            <tbody>
              <tr><td>Title</td>{items.map((i) => <td key={i.id} className="title-cell">{i.title}</td>)}</tr>
              <tr><td>Price (original)</td>{items.map((i) => <td key={i.id}>{money(i.current_price, i.currency_code)}</td>)}</tr>
              <tr><td>Price ({currency})</td>{items.map((i) => <td key={i.id}>{i.converted_price ? money(i.converted_price, currency) : <span className="muted">no FX rate</span>}</td>)}</tr>
              <tr><td>Availability</td>{items.map((i) => <td key={i.id}><StockBadge status={i.stock_status} /></td>)}</tr>
              <tr><td>Rank</td>{items.map((i) => <td key={i.id}>{num(i.sales_rank)}</td>)}</tr>
              <tr><td>Rating</td>{items.map((i) => <td key={i.id}>{i.rating ?? "—"} ({num(i.review_count)})</td>)}</tr>
              <tr><td>Est. profit / unit</td>{items.map((i) => <td key={i.id}>{money(i.profit.profit, i.currency_code)}
                {!i.profit.is_complete && <div className="small muted">missing {i.profit.missing_inputs.map(label).join(", ")}</div>}</td>)}</tr>
              <tr><td>Margin</td>{items.map((i) => <td key={i.id}>{i.profit.margin_pct ? `${i.profit.margin_pct}%` : "—"}</td>)}</tr>
              {metricNames.map((n) => (
                <tr key={n}><td>{label(n)}</td>{items.map((i) => {
                  const m = i.metrics.find((x) => x.name === n);
                  return <td key={i.id}>{m ? <>{m.value !== null ? num(m.value, 2) : ""} <MeasurementBadge type={m.type} /></> : "—"}</td>;
                })}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="card muted">Select 2–10 products in the Product Database and choose “Compare”, or open a match below.</div>}
      <div className="card">
        <div className="row"><h2>Match candidates</h2><span className="spacer" />
          <label className="field"><span>Minimum score</span>
            <select value={minScore} onChange={(e) => setMinScore(e.target.value)}>{["0.55", "0.6", "0.8", "0.95"].map((s) => <option key={s}>{s}</option>)}</select></label>
        </div>
        {matches && !matches.items.length && <Empty>No pending candidates. GTIN matches appear automatically after research jobs.</Empty>}
        <table>
          <tbody>
            {matches?.items.map((m) => (
              <tr key={m.id}>
                <td><span className="badge info">{label(m.match_method)}</span><div className="small muted">{(Number(m.score) * 100).toFixed(0)}%</div></td>
                <td className="title-cell"><Link href={`/products/view/?id=${m.source.id}`}>{m.source.title}</Link><div className="small muted">{m.source.marketplace_code} · {money(m.source.current_price, m.source.currency_code)}</div></td>
                <td className="title-cell"><Link href={`/products/view/?id=${m.target.id}`}>{m.target.title}</Link><div className="small muted">{m.target.marketplace_code} · {money(m.target.current_price, m.target.currency_code)}</div></td>
                <td className="row">
                  <button onClick={() => setIds([m.source.id, m.target.id])}>Compare</button>
                  {can("review_matches") && <><button onClick={() => review(m.id, "confirmed")}>Confirm</button>
                    <button onClick={() => review(m.id, "rejected")}>Reject</button></>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
