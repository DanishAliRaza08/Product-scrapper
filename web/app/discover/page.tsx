"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorAlert } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { Connector, Job, Marketplace, Project } from "@/lib/types";

const INPUTS = [
  { value: "keyword", label: "Keyword" },
  { value: "shop_url", label: "Shop URL" },
  { value: "category_url", label: "Category URL" },
  { value: "product_url", label: "Product URL" },
  { value: "category", label: "Category ID" },
  { value: "listing_ids", label: "Listing IDs" },
] as const;

const EXAMPLES: Record<string, Record<string, string>> = {
  demo: {
    shop_url: "https://demo-marketplace.example/shop/seller7",
    category_url: "https://demo-marketplace.example/c/fitness",
    product_url: "https://demo-marketplace.example/p/DEMO-42",
    category: "kitchen",
    listing_ids: "DEMO-1, DEMO-2, DEMO-3",
  },
  ebay: {
    shop_url: "https://www.ebay.co.uk/str/shopname", category_url: "https://www.ebay.co.uk/b/Yoga-Mats/158932/bn_1",
    product_url: "https://www.ebay.co.uk/itm/123456789012", category: "158932", listing_ids: "123456789012",
  },
  amazon: {
    category_url: "https://www.amazon.co.uk/b?node=319535011", product_url: "https://www.amazon.co.uk/dp/B08N5WRWNW",
    category: "319535011", listing_ids: "B08N5WRWNW",
  },
};

export default function Discover() {
  const router = useRouter();
  const { data: markets } = useApi<Marketplace[]>("/marketplaces");
  const { data: connectors } = useApi<Connector[]>("/connectors");
  const { data: projects } = useApi<Project[]>("/projects");
  const [marketplace, setMarketplace] = useState("DEMO_UK");
  const [inputType, setInputType] = useState<string>("keyword");
  const [value, setValue] = useState("");
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(200);
  const [projectId, setProjectId] = useState("");
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [refreshMode, setRefreshMode] = useState("manual");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const market = markets?.find((m) => m.code === marketplace);
  const connector = useMemo(() => connectors?.find((c) => c.key === market?.platform), [connectors, market]);
  const supported = connector?.supported_inputs ?? [];
  useEffect(() => {
    if (connector && !supported.includes(inputType)) setInputType(supported[0] ?? "keyword");
  }, [connector]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      marketplace_code: marketplace, input_type: inputType, result_limit: limit, refresh_mode: refreshMode,
      project_id: projectId || null,
      filters: { ...(priceMin ? { price_min: priceMin } : {}), ...(priceMax ? { price_max: priceMax } : {}) },
    };
    if (inputType === "keyword") body.keyword = value;
    else if (inputType === "category") body.category = value;
    else if (inputType === "listing_ids") body.listing_ids = value.split(/[\s,]+/).filter(Boolean);
    else { body.url = value; if (keyword) body.keyword = keyword; }
    try {
      const job = await api.post<Job>("/jobs", body, { "Idempotency-Key": crypto.randomUUID() });
      router.push(`/jobs/view/?id=${job.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const example = EXAMPLES[market?.platform ?? ""]?.[inputType];
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Product Discovery</h1>
          <p>Submit a shop, category, product URL or keyword. Jobs run in the cloud and continue if you close the app.</p>
        </div>
      </div>
      <form className="card stack" onSubmit={submit}>
        <ErrorAlert error={error} />
        <div className="form-grid">
          <label className="field"><span>Marketplace</span>
            <select value={marketplace} onChange={(e) => setMarketplace(e.target.value)}>
              {markets?.map((m) => {
                const c = connectors?.find((x) => x.key === m.platform);
                return <option key={m.code} value={m.code} disabled={c ? !c.configured : false}>
                  {m.name}{c && !c.configured ? " (not configured)" : ""}
                </option>;
              })}
            </select>
          </label>
          <label className="field"><span>Input</span>
            <select value={inputType} onChange={(e) => setInputType(e.target.value)}>
              {INPUTS.map((i) => <option key={i.value} value={i.value} disabled={!supported.includes(i.value)}>{i.label}</option>)}
            </select>
          </label>
          <label className="field"><span>Result limit</span>
            <input type="number" min={1} max={100000} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          <label className="field"><span>Add to project</span>
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">— none —</option>
              {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        </div>
        <label className="field">
          <span>{INPUTS.find((i) => i.value === inputType)?.label}</span>
          {inputType === "listing_ids"
            ? <textarea rows={3} required value={value} onChange={(e) => setValue(e.target.value)} placeholder={example} />
            : <input required value={value} onChange={(e) => setValue(e.target.value)} placeholder={example ?? "e.g. yoga mat"} />}
        </label>
        {["shop_url", "category_url"].includes(inputType) && (
          <label className="field"><span>Keyword (optional, some sources require it with a shop)</span>
            <input value={keyword} onChange={(e) => setKeyword(e.target.value)} />
          </label>
        )}
        <div className="form-grid">
          <label className="field"><span>Min price</span><input inputMode="decimal" value={priceMin} onChange={(e) => setPriceMin(e.target.value)} /></label>
          <label className="field"><span>Max price</span><input inputMode="decimal" value={priceMax} onChange={(e) => setPriceMax(e.target.value)} /></label>
          <label className="field"><span>Refresh</span>
            <select value={refreshMode} onChange={(e) => setRefreshMode(e.target.value)}>
              <option value="manual">Manual</option><option value="daily">Daily</option><option value="weekly">Weekly</option>
            </select>
          </label>
        </div>
        <div className="row"><button className="primary" disabled={busy}>{busy ? "Starting…" : "Start job"}</button></div>
      </form>
      {connector && (
        <div className="card">
          <h2>What {connector.display_name} can provide</h2>
          <div className="grid grid-2">
            <div>
              <h3>Available</h3>
              <ul className="small">{connector.fields_available.map((f) => <li key={f}>{f}</li>)}</ul>
            </div>
            <div>
              <h3>Not available from this source</h3>
              <ul className="small">{connector.known_missing_fields.map((f) => <li key={f}>{f}</li>)}</ul>
            </div>
          </div>
          {connector.limitations.length > 0 && (
            <div className="alert info small">{connector.limitations.map((l) => <div key={l}>• {l}</div>)}</div>
          )}
        </div>
      )}
    </div>
  );
}
