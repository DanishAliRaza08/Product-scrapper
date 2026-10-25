"use client";
import { useEffect, useState } from "react";
import { Empty, ErrorAlert, Tabs } from "@/components/ui";
import { api, can } from "@/lib/api";
import { ago, label } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Marketplace } from "@/lib/types";

const ALL_TABS = ["Notifications", "Alerts", "Fees", "Exchange rates", "Users", "Audit log"] as const;
type Tab = (typeof ALL_TABS)[number];

export default function Settings() {
  const tabs = ALL_TABS.filter((t) => (t === "Users" ? can("manage_users") : t === "Audit log" ? can("view_audit") : true));
  const [tab, setTab] = useState<Tab>("Notifications");
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab") as Tab | null;
    if (t && ALL_TABS.includes(t)) setTab(t);
  }, []);
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Settings</h1></div></div>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "Notifications" && <Notifications />}
      {tab === "Alerts" && <Alerts />}
      {tab === "Fees" && <Fees />}
      {tab === "Exchange rates" && <Rates />}
      {tab === "Users" && <Users />}
      {tab === "Audit log" && <Audit />}
    </div>
  );
}

function Notifications() {
  const { data, reload } = useApi<{ id: number; title: string; body: string | null; read_at: string | null; created_at: string;
    data: { listing_id?: number } }[]>("/notifications");
  return (
    <div className="card">
      <div className="row"><h2>Notifications</h2><span className="spacer" /><button onClick={() => api.post("/notifications/read-all").then(reload)}>Mark all read</button></div>
      {data && !data.length && <Empty>No notifications. Create alert rules to be told about price and stock changes.</Empty>}
      {data?.map((n) => (
        <div key={n.id} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
          {!n.read_at && <span className="badge info">new</span>}
          <strong>{n.title}</strong><span className="muted small">{n.body}</span><span className="spacer" />
          {n.data.listing_id && <a href={`/products/view/?id=${n.data.listing_id}`}>Open</a>}
          <span className="small muted">{ago(n.created_at)}</span>
        </div>
      ))}
    </div>
  );
}

function Alerts() {
  const { data, reload } = useApi<{ id: number; rule_type: string; threshold: string | null; listing_id: number | null;
    project_id: string | null }[]>("/alert-rules");
  const { data: projects } = useApi<{ id: string; name: string }[]>("/projects");
  const [form, setForm] = useState({ rule_type: "price_drop_pct", threshold: "10", project_id: "", listing_id: "" });
  const [err, setErr] = useState<unknown>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("/alert-rules", { rule_type: form.rule_type, threshold: form.threshold || null,
        project_id: form.project_id || null, listing_id: form.listing_id ? Number(form.listing_id) : null });
      reload(); setErr(null);
    } catch (x) { setErr(x); }
  }
  return (
    <div className="stack">
      {can("run_jobs") && (
        <form className="card stack" onSubmit={add}>
          <h2>New alert rule</h2>
          <ErrorAlert error={err} />
          <div className="form-grid">
            <label className="field"><span>When</span><select value={form.rule_type} onChange={(e) => setForm({ ...form, rule_type: e.target.value })}>
              {["price_drop_pct", "price_rise_pct", "price_below", "back_in_stock", "out_of_stock", "rank_improved_pct"].map((r) => <option key={r} value={r}>{label(r)}</option>)}</select></label>
            <label className="field"><span>Threshold</span><input value={form.threshold} onChange={(e) => setForm({ ...form, threshold: e.target.value })} /></label>
            <label className="field"><span>For project</span><select value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value, listing_id: "" })}>
              <option value="">—</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <label className="field"><span>…or listing ID</span><input value={form.listing_id} onChange={(e) => setForm({ ...form, listing_id: e.target.value, project_id: "" })} /></label>
          </div>
          <button className="primary">Add rule</button>
        </form>
      )}
      <div className="card"><table><tbody>
        {data?.map((r) => (
          <tr key={r.id}><td>{label(r.rule_type)}</td><td>{r.threshold ?? ""}</td>
            <td>{r.project_id ? `project ${projects?.find((p) => p.id === r.project_id)?.name ?? ""}` : `listing #${r.listing_id}`}</td>
            <td className="right">{can("run_jobs") && <button className="danger" onClick={() => api.del(`/alert-rules/${r.id}`).then(reload)}>Delete</button>}</td></tr>
        ))}
      </tbody></table>{data && !data.length && <Empty>No alert rules.</Empty>}</div>
    </div>
  );
}

function Fees() {
  const { data, reload } = useApi<{ id: number; marketplace_id: number; name: string; category_pattern: string | null; referral_rate: string;
    fixed_fee: string; payment_rate: string; fulfilment_fee: string; currency_code: string; effective_from: string; is_global: boolean }[]>("/fee-profiles");
  const { data: markets } = useApi<Marketplace[]>("/marketplaces");
  const [f, setF] = useState({ marketplace_code: "DEMO_UK", name: "", category_pattern: "", referral_pct: "15", fixed_fee: "0",
    payment_pct: "0", fulfilment_fee: "0", currency_code: "GBP" });
  const [err, setErr] = useState<unknown>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("/fee-profiles", { marketplace_code: f.marketplace_code, name: f.name, category_pattern: f.category_pattern || null,
        referral_rate: String(Number(f.referral_pct) / 100), fixed_fee: f.fixed_fee, payment_rate: String(Number(f.payment_pct) / 100),
        fulfilment_fee: f.fulfilment_fee, currency_code: f.currency_code });
      reload(); setErr(null);
    } catch (x) { setErr(x); }
  }
  const field = (k: keyof typeof f, l: string) => <label className="field" key={k}><span>{l}</span><input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  return (
    <div className="stack">
      <div className="alert info small">Marketplace fee schedules change. Enter the current published fees for your category; profits are only as accurate as these inputs.</div>
      {can("edit_costs") && (
        <form className="card stack" onSubmit={add}>
          <h2>Add fee profile</h2><ErrorAlert error={err} />
          <div className="form-grid">
            <label className="field"><span>Marketplace</span><select value={f.marketplace_code} onChange={(e) => setF({ ...f, marketplace_code: e.target.value,
              currency_code: markets?.find((m) => m.code === e.target.value)?.currency_code ?? f.currency_code })}>
              {markets?.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}</select></label>
            {field("name", "Name")}{field("category_pattern", "Category contains (optional)")}{field("referral_pct", "Referral %")}
            {field("fixed_fee", "Fixed fee")}{field("payment_pct", "Payment %")}{field("fulfilment_fee", "Fulfilment fee")}{field("currency_code", "Currency")}
          </div>
          <button className="primary">Save</button>
        </form>
      )}
      <div className="table-wrap"><table>
        <thead><tr><th>Marketplace</th><th>Name</th><th>Category</th><th className="num">Referral</th><th className="num">Fixed</th><th className="num">Payment</th><th className="num">Fulfilment</th><th>From</th><th /></tr></thead>
        <tbody>{data?.map((p) => (
          <tr key={p.id}><td>{markets?.find((m) => m.id === p.marketplace_id)?.code}</td><td>{p.name}</td><td>{p.category_pattern ?? "any"}</td>
            <td className="num">{(Number(p.referral_rate) * 100).toFixed(1)}%</td><td className="num">{p.fixed_fee} {p.currency_code}</td>
            <td className="num">{(Number(p.payment_rate) * 100).toFixed(1)}%</td><td className="num">{p.fulfilment_fee}</td><td>{p.effective_from}</td>
            <td>{!p.is_global && can("edit_costs") && <button className="danger" onClick={() => api.del(`/fee-profiles/${p.id}`).then(reload)}>Delete</button>}</td></tr>
        ))}</tbody></table></div>
    </div>
  );
}

function Rates() {
  const { data, reload } = useApi<{ id: number; source: string; base_currency: string; quote_currency: string; rate: string; effective_at: string }[]>("/exchange-rates?limit=50");
  const [f, setF] = useState({ base_currency: "GBP", quote_currency: "USD", rate: "", effective_at: new Date().toISOString().slice(0, 10) });
  const [err, setErr] = useState<unknown>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try { await api.post("/exchange-rates", { ...f, effective_at: `${f.effective_at}T00:00:00Z` }); reload(); setErr(null); } catch (x) { setErr(x); }
  }
  return (
    <div className="stack">
      <p className="secondary">ECB reference rates are imported daily by the scheduler. Add manual rates for other sources. Conversions always keep the original amount and record the rate used.</p>
      {can("edit_costs") && (
        <form className="card row" onSubmit={add}>
          <ErrorAlert error={err} />
          <input aria-label="Base" value={f.base_currency} onChange={(e) => setF({ ...f, base_currency: e.target.value.toUpperCase() })} size={4} />
          →<input aria-label="Quote" value={f.quote_currency} onChange={(e) => setF({ ...f, quote_currency: e.target.value.toUpperCase() })} size={4} />
          <input aria-label="Rate" required placeholder="rate" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} />
          <input aria-label="Effective" type="date" value={f.effective_at} onChange={(e) => setF({ ...f, effective_at: e.target.value })} />
          <button>Add rate</button>
        </form>
      )}
      <div className="table-wrap"><table><thead><tr><th>Pair</th><th className="num">Rate</th><th>Source</th><th>Effective</th></tr></thead>
        <tbody>{data?.map((r) => <tr key={r.id}><td>{r.base_currency}/{r.quote_currency}</td><td className="num">{r.rate}</td><td>{r.source}</td><td>{new Date(r.effective_at).toLocaleDateString()}</td></tr>)}</tbody></table></div>
    </div>
  );
}

function Users() {
  const { data, reload } = useApi<{ id: string; email: string; full_name: string | null; role: string; is_active: boolean }[]>("/users");
  const [f, setF] = useState({ email: "", password: "", role: "researcher" });
  const [err, setErr] = useState<unknown>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try { await api.post("/users", f); setF({ email: "", password: "", role: "researcher" }); reload(); setErr(null); } catch (x) { setErr(x); }
  }
  const ROLES = ["admin", "research_manager", "researcher", "viewer"];
  return (
    <div className="stack">
      <form className="card row" onSubmit={add}>
        <ErrorAlert error={err} />
        <input type="email" required placeholder="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        <input type="password" required minLength={12} placeholder="initial password (12+)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} aria-label="Role">{ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}</select>
        <button className="primary">Add user</button>
      </form>
      <div className="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Active</th></tr></thead>
        <tbody>{data?.map((u) => (
          <tr key={u.id}><td>{u.email}</td>
            <td><select value={u.role} onChange={(e) => api.patch(`/users/${u.id}`, { role: e.target.value }).then(reload).catch(setErr)} aria-label="Role">
              {ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}</select></td>
            <td><input type="checkbox" checked={u.is_active} aria-label="Active" onChange={(e) => api.patch(`/users/${u.id}`, { is_active: e.target.checked }).then(reload).catch(setErr)} /></td></tr>
        ))}</tbody></table></div>
    </div>
  );
}

function Audit() {
  const { data } = useApi<{ id: number; action: string; resource_type: string | null; resource_id: string | null; ip_address: string | null;
    details: Record<string, unknown>; created_at: string }[]>("/audit-logs?limit=200");
  return (
    <div className="table-wrap"><table><thead><tr><th>When</th><th>Action</th><th>Resource</th><th>IP</th><th>Details</th></tr></thead>
      <tbody>{data?.map((a) => <tr key={a.id}><td className="small">{new Date(a.created_at).toLocaleString()}</td><td>{a.action}</td>
        <td className="mono small">{a.resource_type} {a.resource_id}</td><td className="mono small">{a.ip_address ?? ""}</td>
        <td className="mono small">{JSON.stringify(a.details)}</td></tr>)}</tbody></table></div>
  );
}
