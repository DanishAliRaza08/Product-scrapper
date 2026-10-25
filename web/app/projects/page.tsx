"use client";
import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorAlert } from "@/components/ui";
import { api, can, qs } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Marketplace, Project } from "@/lib/types";

type Saved = { id: number; name: string; kind: string; parameters: Record<string, unknown>; created_at: string };

export default function Projects() {
  const { data: projects, reload, error } = useApi<Project[]>("/projects");
  const { data: saved, reload: reloadSaved } = useApi<Saved[]>("/saved-searches");
  const { data: markets } = useApi<Marketplace[]>("/marketplaces");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [shop, setShop] = useState({ project: "", marketplace: "DEMO_UK", url: "" });
  const [err, setErr] = useState<unknown>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try { await api.post("/projects", { name, description: desc || null }); setName(""); setDesc(""); reload(); }
    catch (x) { setErr(x); }
  }
  async function addShop(e: React.FormEvent) {
    e.preventDefault();
    try { await api.post(`/projects/${shop.project}/shops`, { marketplace_code: shop.marketplace, shop_url: shop.url }); setShop({ ...shop, url: "" }); setErr(null); }
    catch (x) { setErr(x); }
  }
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Projects &amp; Saved</h1><p>Group research, track competitor shops and reuse searches.</p></div></div>
      <ErrorAlert error={error ?? err} />
      {can("manage_projects") && (
        <form className="card row" onSubmit={create}>
          <input required placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Description" value={desc} onChange={(e) => setDesc(e.target.value)} style={{ flex: 1 }} />
          <button className="primary">Create project</button>
        </form>
      )}
      <div className="grid grid-2">
        {projects?.map((p) => (
          <div className="card" key={p.id}>
            <div className="row"><h2>{p.name}</h2><span className="spacer" /><span className="muted small">{ago(p.created_at)}</span></div>
            {p.description && <p className="secondary">{p.description}</p>}
            <div className="row">
              <Link href={`/products/?project_id=${p.id}`}>{num(p.product_count)} products →</Link>
              <Link href={`/opportunities/`}>Opportunities</Link>
              {can("manage_projects") && <button onClick={() => api.post(`/projects/${p.id}/archive`).then(reload)}>Archive</button>}
            </div>
          </div>
        ))}
        {projects && !projects.length && <Empty>No projects yet.</Empty>}
      </div>
      {can("manage_projects") && projects && projects.length > 0 && (
        <form className="card stack" onSubmit={addShop}>
          <h2>Track a competitor shop</h2>
          <div className="form-grid">
            <select required value={shop.project} onChange={(e) => setShop({ ...shop, project: e.target.value })} aria-label="Project">
              <option value="">Project…</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
            <select value={shop.marketplace} onChange={(e) => setShop({ ...shop, marketplace: e.target.value })} aria-label="Marketplace">
              {markets?.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}</select>
            <input required placeholder="Shop URL" value={shop.url} onChange={(e) => setShop({ ...shop, url: e.target.value })} />
          </div>
          <button>Add shop</button>
        </form>
      )}
      <div className="card">
        <h2>Saved searches and views</h2>
        {saved && !saved.length && <p className="muted">Save a view from the Product Database to reuse its filters.</p>}
        <table><tbody>
          {saved?.map((s) => (
            <tr key={s.id}>
              <td>{s.name}</td><td className="small muted">{s.kind.replace("_", " ")}</td>
              <td>{s.kind === "product_filter"
                ? <Link href={`/products/${qs(s.parameters as Record<string, string>)}`}>Open</Link>
                : <span className="mono small">{JSON.stringify(s.parameters)}</span>}</td>
              <td className="right">{can("manage_projects") && <button className="danger" onClick={() => api.del(`/saved-searches/${s.id}`).then(reloadSaved)}>Delete</button>}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </div>
  );
}
