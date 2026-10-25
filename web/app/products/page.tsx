"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Empty, ErrorAlert, RemoteImage, SourceBadge, StockBadge } from "@/components/ui";
import { api, can, qs } from "@/lib/api";
import { ago, money, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Listing, Marketplace, Page, Project } from "@/lib/types";

type Filters = {
  q: string; marketplace: string; stock_status: string; source_type: string; category: string; brand: string;
  price_min: string; price_max: string; rating_min: string; rank_max: string; project_id: string; job_id: string;
  watched: string; sort: string;
};
const EMPTY: Filters = { q: "", marketplace: "", stock_status: "", source_type: "", category: "", brand: "", price_min: "",
  price_max: "", rating_min: "", rank_max: "", project_id: "", job_id: "", watched: "", sort: "updated" };

const COLUMNS = ["Image", "Title", "Marketplace", "SKU / ID", "Category", "Price", "Rank", "Rating", "Reviews",
  "Availability", "Source", "Updated"] as const;
type Column = (typeof COLUMNS)[number];
const COL_KEY = "mpi.products.columns";

function loadColumns(): Column[] {
  try {
    const raw = localStorage.getItem(COL_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* storage unavailable: use defaults */ }
  return [...COLUMNS];
}

export default function Products() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [cursors, setCursors] = useState<string[]>([]);
  const [columns, setColumns] = useState<Column[]>([...COLUMNS]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [targetProject, setTargetProject] = useState("");
  const [tag, setTag] = useState("");

  useEffect(() => {
    setColumns(loadColumns());
    const sp = new URLSearchParams(window.location.search);
    const init = { ...EMPTY };
    (Object.keys(EMPTY) as (keyof Filters)[]).forEach((k) => { const v = sp.get(k === "job_id" ? "job" : k); if (v) init[k] = v; });
    setFilters(init);
    setApplied(init);
  }, []);
  useEffect(() => { try { localStorage.setItem(COL_KEY, JSON.stringify(columns)); } catch { /* ignore */ } }, [columns]);

  const { data: markets } = useApi<Marketplace[]>("/marketplaces");
  const { data: projects } = useApi<Project[]>("/projects");
  const cursor = cursors[cursors.length - 1] ?? null;
  const path = useMemo(() => `/products${qs({ ...applied, limit: 50, cursor, with_total: !cursor })}`, [applied, cursor]);
  const { data, error, loading, reload } = useApi<Page<Listing>>(path, [path]);
  const [total, setTotal] = useState<{ n: number; est: boolean } | null>(null);
  useEffect(() => { if (data?.total !== null && data?.total !== undefined) setTotal({ n: data.total, est: data.total_is_estimate }); }, [data]);

  const show = (c: Column) => columns.includes(c);
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setFilters({ ...filters, [k]: e.target.value });
  const apply = (f = filters) => { setApplied(f); setCursors([]); setSelected(new Set()); };
  const toggle = (id: number) => { const s = new Set(selected); if (s.has(id)) s.delete(id); else s.add(id); setSelected(s); };
  const allOnPage = data?.items.every((i) => selected.has(i.id)) && !!data?.items.length;

  async function bulk(kind: "project" | "watch" | "export") {
    setActionError(null);
    setMessage(null);
    try {
      if (kind === "project") {
        if (!targetProject) return;
        const r = await api.post<{ added: number }>(`/projects/${targetProject}/products`, {
          listing_ids: [...selected], tags: tag ? tag.split(",").map((t) => t.trim()).filter(Boolean) : [],
        });
        setMessage(`Added ${r.added} products to the project.`);
      } else if (kind === "watch") {
        await Promise.all([...selected].map((id) => api.patch(`/products/${id}`, { is_watched: true })));
        setMessage(`Watching ${selected.size} products: they are refreshed more often and drive alerts.`);
        reload();
      } else {
        const { cursor: _c, ...rest } = { ...applied, cursor: null };
        const f = Object.fromEntries(Object.entries(rest).filter(([, v]) => v !== ""));
        await api.post("/exports", { export_format: "xlsx", filters: { ...f, marketplace: f.marketplace ? [f.marketplace] : undefined,
          stock_status: f.stock_status ? [f.stock_status] : undefined, source_type: f.source_type ? [f.source_type] : undefined } });
        setMessage("Export started. Find it under Exports.");
      }
    } catch (e) {
      setActionError(e);
    }
  }

  async function saveView() {
    const name = prompt("Name this view");
    if (!name) return;
    await api.post("/saved-searches", { name, kind: "product_filter", parameters: applied }).catch(setActionError);
    setMessage(`Saved view “${name}”.`);
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Product Database</h1>
          <p>{total ? `${num(total.n)}${total.est ? "+" : ""} products match` : "Search, filter and compare tracked listings."}</p>
        </div>
        <div className="row">
          <button onClick={saveView}>Save view</button>
          {can("export") && <button onClick={() => bulk("export")}>Export results</button>}
        </div>
      </div>
      <form className="card stack" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <div className="form-grid">
          <label className="field"><span>Search</span><input value={filters.q} onChange={set("q")} placeholder="Title, brand, SKU, GTIN…" /></label>
          <label className="field"><span>Marketplace</span>
            <select value={filters.marketplace} onChange={set("marketplace")}>
              <option value="">All</option>{markets?.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Availability</span>
            <select value={filters.stock_status} onChange={set("stock_status")}>
              <option value="">Any</option><option value="in_stock">In stock</option><option value="low_stock">Low stock</option>
              <option value="out_of_stock">Out of stock</option><option value="unknown">Unknown</option>
            </select>
          </label>
          <label className="field"><span>Source</span>
            <select value={filters.source_type} onChange={set("source_type")}>
              <option value="">Any</option><option value="competitor_research">Competitor research</option>
              {can("view_own_account_data") && <option value="own_account">Own account</option>}
            </select>
          </label>
          <label className="field"><span>Project</span>
            <select value={filters.project_id} onChange={set("project_id")}>
              <option value="">Any</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Category contains</span><input value={filters.category} onChange={set("category")} /></label>
          <label className="field"><span>Brand</span><input value={filters.brand} onChange={set("brand")} /></label>
          <label className="field"><span>Price from</span><input inputMode="decimal" value={filters.price_min} onChange={set("price_min")} /></label>
          <label className="field"><span>Price to</span><input inputMode="decimal" value={filters.price_max} onChange={set("price_max")} /></label>
          <label className="field"><span>Min rating</span><input inputMode="decimal" value={filters.rating_min} onChange={set("rating_min")} /></label>
          <label className="field"><span>Rank better than</span><input inputMode="numeric" value={filters.rank_max} onChange={set("rank_max")} /></label>
          <label className="field"><span>Sort</span>
            <select value={filters.sort} onChange={set("sort")}>
              <option value="updated">Recently updated</option><option value="price_asc">Price ↑</option>
              <option value="price_desc">Price ↓</option><option value="rank">Best rank</option>
              <option value="reviews">Most reviews</option><option value="title">Title A–Z</option>
            </select>
          </label>
        </div>
        <div className="row">
          <button className="primary">Apply</button>
          <button type="button" onClick={() => { setFilters(EMPTY); apply(EMPTY); }}>Reset</button>
          {applied.job_id && <span className="badge info">Filtered to one job</span>}
          <span className="spacer" />
          <details>
            <summary className="small">Columns</summary>
            <div className="card" style={{ position: "absolute", zIndex: 10 }}>
              {COLUMNS.map((c) => (
                <label key={c} className="row small">
                  <input type="checkbox" checked={show(c)}
                    onChange={() => setColumns(show(c) ? columns.filter((x) => x !== c) : COLUMNS.filter((x) => x === c || columns.includes(x)))} />
                  {c}
                </label>
              ))}
            </div>
          </details>
        </div>
      </form>
      {selected.size > 0 && (
        <div className="card row">
          <strong>{selected.size} selected</strong>
          {can("manage_projects") && (
            <>
              <select value={targetProject} onChange={(e) => setTargetProject(e.target.value)} aria-label="Project">
                <option value="">Add to project…</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <input placeholder="tags, comma separated" value={tag} onChange={(e) => setTag(e.target.value)} />
              <button disabled={!targetProject} onClick={() => bulk("project")}>Add</button>
            </>
          )}
          {can("run_jobs") && <button onClick={() => bulk("watch")}>Watch</button>}
          {selected.size >= 2 && selected.size <= 10 &&
            <Link className="btn" href={`/compare/?ids=${[...selected].join(",")}`}>Compare</Link>}
          <button className="link" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
      {message && <div className="alert info">{message}</div>}
      <ErrorAlert error={error ?? actionError} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="Select page" checked={allOnPage}
                onChange={() => { const s = new Set(selected); data?.items.forEach((i) => (allOnPage ? s.delete(i.id) : s.add(i.id))); setSelected(s); }} /></th>
              {COLUMNS.filter(show).map((c) => <th key={c} className={["Price", "Rank", "Rating", "Reviews"].includes(c) ? "num" : ""}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((p) => (
              <tr key={p.id}>
                <td><input type="checkbox" aria-label={`Select ${p.title}`} checked={selected.has(p.id)} onChange={() => toggle(p.id)} /></td>
                {show("Image") && <td><div className="thumb"><RemoteImage className="thumb" src={p.main_image_url} /></div></td>}
                {show("Title") && <td className="title-cell"><Link href={`/products/view/?id=${p.id}`}>{p.title}</Link>
                  {p.is_watched && <span className="badge info" style={{ marginLeft: 6 }}>watched</span>}
                  {p.brand && <div className="small muted">{p.brand}</div>}</td>}
                {show("Marketplace") && <td>{p.marketplace_code}<div className="small muted">{p.country_code}</div></td>}
                {show("SKU / ID") && <td className="mono">{p.seller_sku ?? p.external_listing_id}</td>}
                {show("Category") && <td className="small">{p.category_path ?? "—"}</td>}
                {show("Price") && <td className="num">{money(p.current_price, p.currency_code)}</td>}
                {show("Rank") && <td className="num">{num(p.sales_rank)}</td>}
                {show("Rating") && <td className="num">{p.rating ?? "—"}</td>}
                {show("Reviews") && <td className="num">{num(p.review_count)}</td>}
                {show("Availability") && <td><StockBadge status={p.stock_status} /></td>}
                {show("Source") && <td><SourceBadge source={p.source_type} /></td>}
                {show("Updated") && <td className="small muted" title={p.last_seen_at}>{ago(p.last_seen_at)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && <Empty>No products match these filters. <Link href="/discover/">Run a research job</Link>.</Empty>}
        {loading && !data && <Empty>Loading…</Empty>}
      </div>
      <div className="row">
        <button disabled={!cursors.length} onClick={() => setCursors(cursors.slice(0, -1))}>Previous</button>
        <button disabled={!data?.next_cursor} onClick={() => data?.next_cursor && setCursors([...cursors, data.next_cursor])}>Next</button>
        <span className="small muted">Page {cursors.length + 1}</span>
      </div>
    </div>
  );
}
