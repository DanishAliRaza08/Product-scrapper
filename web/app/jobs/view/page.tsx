"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Empty, ErrorAlert, JobStatusBadge, Progress, Stat } from "@/components/ui";
import { api, can, qs, request } from "@/lib/api";
import { notify } from "@/lib/desktop";
import { ago, label, num } from "@/lib/format";
import { useApi, useQueryParam } from "@/lib/hooks";
import type { Job, JobEvent, Page } from "@/lib/types";

type Item = { id: number; external_product_id: string; listing_id: number | null; item_status: string; attempt_count: number;
  error_code: string | null; error_message: string | null; processed_at: string | null };
const TERMINAL = ["completed", "partial", "failed", "cancelled", "paused"];

export default function JobView() {
  const id = useQueryParam("id");
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [itemStatus, setItemStatus] = useState("");
  const lastEvent = useRef(0);
  const wasRunning = useRef(false);
  const { data: items, reload: reloadItems } = useApi<Page<Item>>(
    id ? `/jobs/${id}/items${qs({ status: itemStatus, limit: 100 })}` : null, [itemStatus]);

  const poll = useCallback(async () => {
    if (!id) return;
    const [j, ev] = await Promise.all([
      api.get<Job>(`/jobs/${id}`),
      api.get<JobEvent[]>(`/jobs/${id}/events?after_id=${lastEvent.current}`),
    ]);
    if (ev.length) {
      lastEvent.current = ev[ev.length - 1].id;
      setEvents((prev) => [...prev, ...ev].slice(-300));
    }
    setJob(j);
    return j;
  }, [id]);

  // Progress: polling with a short interval (the API also offers an SSE stream at
  // /jobs/{id}/stream for clients that prefer push).
  useEffect(() => {
    if (!id) return;
    let stop = false;
    const tick = async () => {
      try {
        const j = await poll();
        if (j && !TERMINAL.includes(j.job_status)) wasRunning.current = true;
        if (j && TERMINAL.includes(j.job_status)) {
          if (wasRunning.current) notify("Job finished", `${j.search_keyword ?? j.input_url ?? "Job"}: ${j.job_status}`);
          reloadItems();
          return;
        }
      } catch (e) {
        setError(e);
      }
      if (!stop) setTimeout(tick, 2000);
    };
    tick();
    return () => { stop = true; };
  }, [id, poll]); // eslint-disable-line react-hooks/exhaustive-deps

  async function action(path: string, body?: unknown) {
    try {
      setJob(await request<Job>("POST", `/jobs/${id}/${path}`, body ?? {}));
      if (path === "resume") { wasRunning.current = true; setTimeout(poll, 500); }
    } catch (e) {
      setError(e);
    }
  }

  if (!job) return <div className="muted">{error ? <ErrorAlert error={error} /> : "Loading…"}</div>;
  const running = !TERMINAL.includes(job.job_status);
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{job.search_keyword ?? job.input_url ?? label(job.job_type)}</h1>
          <p>{job.marketplace_code} · {label(job.input_type)} · created {ago(job.created_at)}</p>
        </div>
        <div className="row">
          <JobStatusBadge status={job.job_status} />
          {can("run_jobs") && running && <button className="danger" onClick={() => action("cancel")}>Cancel</button>}
          {can("run_jobs") && ["paused", "failed", "cancelled", "partial"].includes(job.job_status) &&
            <button onClick={() => action("resume", { retry_failed: true })}>Resume{job.products_failed ? " & retry failed" : ""}</button>}
          {job.products_processed > 0 && <Link className="btn" href={`/products/?job=${job.id}`}>View products</Link>}
        </div>
      </div>
      <ErrorAlert error={error} />
      {job.error_summary && <div className="alert warn">{job.error_summary}</div>}
      {job.source_limit_note && <div className="alert info small">Source limit: {job.source_limit_note}</div>}
      <div className="card stack">
        <Progress pct={job.progress_pct} />
        <div className="grid grid-4">
          <Stat label="Discovered" value={num(job.products_discovered)} hint={job.discovery_complete ? "discovery complete" : "discovering…"} />
          <Stat label="Processed" value={num(job.products_processed)} />
          <Stat label="Failed" value={num(job.products_failed)} hint="retried with backoff first" />
          <Stat label="Unavailable / skipped" value={num(job.products_skipped)} />
          <Stat label="Duplicates ignored" value={num(job.duplicates)} />
        </div>
      </div>
      <div className="grid grid-2">
        <div className="card">
          <div className="row">
            <h2>Items</h2><span className="spacer" />
            <select value={itemStatus} onChange={(e) => setItemStatus(e.target.value)} aria-label="Filter items">
              {["", "succeeded", "failed", "unavailable", "pending"].map((s) => <option key={s} value={s}>{s || "All"}</option>)}
            </select>
          </div>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table>
              <thead><tr><th>External ID</th><th>Status</th><th className="num">Attempts</th><th>Error</th></tr></thead>
              <tbody>
                {items?.items.map((i) => (
                  <tr key={i.id}>
                    <td className="mono">{i.listing_id ? <Link href={`/products/view/?id=${i.listing_id}`}>{i.external_product_id}</Link> : i.external_product_id}</td>
                    <td>{label(i.item_status)}</td>
                    <td className="num">{i.attempt_count}</td>
                    <td className="small">{i.error_message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items && items.items.length === 0 && <Empty>No items.</Empty>}
          </div>
          {items?.total ? <p className="small muted">Showing {items.items.length} of {num(items.total)}</p> : null}
        </div>
        <div className="card">
          <h2>Timeline</h2>
          <div style={{ maxHeight: 520, overflowY: "auto" }}>
            {[...events].reverse().map((e) => (
              <div key={e.id} className="small" style={{ padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                <span className="muted">{new Date(e.created_at).toLocaleTimeString()}</span>{" "}
                <span className={`badge ${e.level === "error" ? "bad" : e.level === "warning" ? "warn" : "neutral"}`}>{label(e.event_type)}</span>{" "}
                {e.message}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
