"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Empty, ErrorAlert } from "@/components/ui";
import { api, can } from "@/lib/api";
import { ago, label } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Marketplace } from "@/lib/types";

type Conn = { id: string; marketplace_code: string; account_name: string; external_account_id: string | null;
  connection_status: string; granted_scopes: string[]; last_sync_at: string | null; last_error: string | null;
  authorization_url: string | null;
  checkpoints: { stream: string; last_successful_sync_at: string | null; retry_count: number; last_error: string | null }[] };

export default function Connections() {
  const { data, reload, error } = useApi<Conn[]>("/connections");
  const { data: markets } = useApi<Marketplace[]>("/marketplaces");
  const [form, setForm] = useState({ marketplace_code: "DEMO_UK", account_name: "" });
  const [err, setErr] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    if (sp.get("connected")) setNotice("Account connected. Run a sync to import catalogue and orders.");
    if (sp.get("error")) setErr(new Error(`Connection failed: ${label(sp.get("error"))}`));
  }, []);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    try {
      const c = await api.post<Conn>("/connections", form);
      if (c.authorization_url) window.location.href = c.authorization_url; // marketplace consent screen
    } catch (x) { setErr(x); }
  }
  async function sync(id: string) {
    try { const r = await api.post<{ job_id: string }>(`/connections/${id}/sync`); setNotice(`Sync started.`); window.location.href = `/jobs/view/?id=${r.job_id}`; }
    catch (x) { setErr(x); }
  }
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Own Seller Accounts</h1>
        <p>Authorised connections to your own marketplace accounts. Credentials stay in the secrets store; only the permissions you approve are requested.</p></div></div>
      {notice && <div className="alert info">{notice}</div>}
      <ErrorAlert error={error ?? err} />
      {can("manage_connections") && (
        <form className="card row" onSubmit={connect}>
          <select value={form.marketplace_code} onChange={(e) => setForm({ ...form, marketplace_code: e.target.value })} aria-label="Marketplace">
            {markets?.filter((m) => ["demo", "ebay", "amazon"].includes(m.platform)).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
          </select>
          <input required placeholder="Account name (e.g. Main UK store)" value={form.account_name} onChange={(e) => setForm({ ...form, account_name: e.target.value })} />
          <button className="primary">Connect account</button>
        </form>
      )}
      {data && !data.length && <Empty>No seller accounts connected.</Empty>}
      {data?.map((c) => (
        <div className="card" key={c.id}>
          <div className="row">
            <h2>{c.account_name}</h2><span className="muted">{c.marketplace_code}</span>
            <span className={`badge ${c.connection_status === "connected" ? "good" : c.connection_status === "pending" ? "neutral" : "bad"}`}>{c.connection_status}</span>
            <span className="spacer" />
            {can("manage_connections") && c.connection_status === "connected" && <button onClick={() => sync(c.id)}>Sync now</button>}
            {can("manage_connections") && c.connection_status !== "revoked" &&
              <button className="danger" onClick={() => confirm("Disconnect and delete stored credentials?") && api.del(`/connections/${c.id}`).then(reload)}>Disconnect</button>}
          </div>
          <p className="small secondary">Seller ID: {c.external_account_id ?? "—"} · Last sync: {ago(c.last_sync_at)} · Scopes: {c.granted_scopes.join(", ") || "—"}</p>
          {c.last_error && <div className="alert error small">{c.last_error}</div>}
          {c.checkpoints.length > 0 && (
            <table><thead><tr><th>Stream</th><th>Last successful sync</th><th>Retries</th><th>Last error</th></tr></thead>
              <tbody>{c.checkpoints.map((cp) => <tr key={cp.stream}><td>{cp.stream}</td><td>{cp.last_successful_sync_at ? new Date(cp.last_successful_sync_at).toLocaleString() : "—"}</td>
                <td>{cp.retry_count}</td><td className="small">{cp.last_error ?? ""}</td></tr>)}</tbody></table>
          )}
          <Link className="small" href="/products/?source_type=own_account">View own-account products →</Link>
        </div>
      ))}
    </div>
  );
}
