"use client";
import { ErrorAlert } from "@/components/ui";
import { label } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Connector } from "@/lib/types";

const STATUS: Record<string, string> = { stable: "good", beta: "info", sandbox: "neutral", planned: "warn" };

export default function Connectors() {
  const { data, error } = useApi<Connector[]>("/connectors");
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Marketplace Connections</h1>
        <p>Capability matrix: what each connector can provide, what it cannot, and how its metrics are classified.</p></div></div>
      <ErrorAlert error={error} />
      {data?.map((c) => (
        <div className="card" key={c.key}>
          <div className="row">
            <h2>{c.display_name}</h2>
            <span className={`badge ${STATUS[c.release_status] ?? "neutral"}`}>{c.release_status}</span>
            <span className={`badge ${c.configured ? "good" : "unavailable"}`}>{c.configured ? "configured" : "not configured"}</span>
            {c.health && <span className="badge neutral">health: {c.health}</span>}
          </div>
          {!c.configured && c.configuration_hint && <div className="alert info small">{c.configuration_hint}</div>}
          <div className="grid grid-2 small">
            <dl className="kv">
              <dt>Marketplaces</dt><dd>{c.marketplaces.join(", ")}</dd>
              <dt>Inputs</dt><dd>{c.supported_inputs.map(label).join(", ")}</dd>
              <dt>Own-account streams</dt><dd>{c.own_account_streams.join(", ") || "—"}</dd>
              <dt>Max results / query</dt><dd>{c.max_results_per_query?.toLocaleString() ?? "—"}</dd>
              <dt>Rate limit</dt><dd>{c.rate_limit.requests_per_second} req/s</dd>
              <dt>Licence</dt><dd>{c.data_licence_note}</dd>
            </dl>
            <div>
              <h3>Metric provenance</h3>
              {Object.entries(c.metric_provenance).map(([k, v]) => <div key={k}>{label(k)} <span className={`badge ${v}`}>{v}</span></div>)}
              <h3 style={{ marginTop: 8 }}>Not provided</h3>
              <div className="muted">{c.known_missing_fields.join(", ") || "—"}</div>
              {c.limitations.length > 0 && <><h3 style={{ marginTop: 8 }}>Limitations</h3><ul>{c.limitations.map((l) => <li key={l}>{l}</li>)}</ul></>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
