"use client";
import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorAlert } from "@/components/ui";
import { qs } from "@/lib/api";
import { label, money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Listing, Opportunity, Project } from "@/lib/types";

type Item = Opportunity & { listing: Listing };

export default function Opportunities() {
  const [projectId, setProjectId] = useState("");
  const { data: projects } = useApi<Project[]>("/projects");
  const { data, error, loading } = useApi<{ items: Item[]; candidates_scored: number }>(
    `/analytics/opportunities${qs({ project_id: projectId, limit: 30 })}`, [projectId]);
  return (
    <div className="stack">
      <div className="page-head">
        <div><h1>Product Opportunities</h1>
          <p>0–100 score from demand, competition, margin, price stability, listing quality and your own assessments. Missing data lowers confidence and never raises the score.</p></div>
        <label className="field"><span>Project</span>
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">All tracked products</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></label>
      </div>
      <ErrorAlert error={error} />
      {loading && <div className="muted">Scoring…</div>}
      {data && !data.items.length && <Empty>No products to score yet.</Empty>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Product</th><th className="num">Score</th><th>Confidence</th><th>Strongest signals</th><th>Missing</th><th className="num">Price</th></tr></thead>
          <tbody>
            {data?.items.map((i) => {
              const top = i.components.filter((c) => c.score !== null).sort((a, b) => (b.score! * b.weight) - (a.score! * a.weight)).slice(0, 2);
              return (
                <tr key={i.listing.id}>
                  <td className="title-cell"><Link href={`/products/view/?id=${i.listing.id}`}>{i.listing.title}</Link><div className="small muted">{i.listing.marketplace_code}</div></td>
                  <td className="num"><strong>{i.score.toFixed(0)}</strong></td>
                  <td><span className={`badge ${i.confidence >= 0.7 ? "good" : i.confidence >= 0.4 ? "warn" : "bad"}`}>{(i.confidence * 100).toFixed(0)}%</span></td>
                  <td className="small">{top.map((c) => <div key={c.name}>{label(c.name)}: {c.evidence}</div>)}</td>
                  <td className="small muted">{i.missing.map(label).join(", ") || "—"}</td>
                  <td className="num">{money(i.listing.current_price, i.listing.currency_code)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {data && <p className="small muted">Scored the {data.candidates_scored} most relevant candidates (watched and best-ranked first).</p>}
    </div>
  );
}
