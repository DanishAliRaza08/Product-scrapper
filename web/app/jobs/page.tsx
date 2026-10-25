"use client";
import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorAlert, JobStatusBadge, Progress } from "@/components/ui";
import { qs } from "@/lib/api";
import { ago, label, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Job, Page } from "@/lib/types";

const STATUSES = ["", "running", "queued", "completed", "partial", "failed", "paused", "cancelled"];

export default function Jobs() {
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const { data, error } = useApi<Page<Job>>(`/jobs${qs({ status, cursor, limit: 25 })}`, [status, cursor]);
  return (
    <div className="stack">
      <div className="page-head">
        <div><h1>Extraction Jobs</h1><p>Research, refresh and own-account sync jobs with item-level outcomes.</p></div>
        <Link className="btn" href="/discover/">New job</Link>
      </div>
      <div className="row">
        <label className="field"><span>Status</span>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setCursors([]); }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? label(s) : "All"}</option>)}
          </select>
        </label>
      </div>
      <ErrorAlert error={error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Input</th><th>Type</th><th>Marketplace</th><th>Status</th><th>Progress</th>
            <th className="num">Processed</th><th className="num">Failed</th><th className="num">Unavailable</th><th>Created</th></tr></thead>
          <tbody>
            {data?.items.map((j) => (
              <tr key={j.id}>
                <td className="title-cell"><Link href={`/jobs/view/?id=${j.id}`}>{j.search_keyword ?? j.input_url ?? j.category ?? label(j.input_type)}</Link></td>
                <td>{label(j.job_type)}</td>
                <td>{j.marketplace_code}</td>
                <td><JobStatusBadge status={j.job_status} /></td>
                <td style={{ minWidth: 120 }}><Progress pct={j.progress_pct} /></td>
                <td className="num">{num(j.products_processed)} / {num(j.products_discovered)}</td>
                <td className="num">{num(j.products_failed)}</td>
                <td className="num">{num(j.products_skipped)}</td>
                <td className="muted">{ago(j.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && <Empty>No jobs match.</Empty>}
      </div>
      <div className="row">
        <button disabled={!cursors.length} onClick={() => setCursors(cursors.slice(0, -1))}>Previous</button>
        <button disabled={!data?.next_cursor} onClick={() => data?.next_cursor && setCursors([...cursors, data.next_cursor])}>Next</button>
      </div>
    </div>
  );
}

