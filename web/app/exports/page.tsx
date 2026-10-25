"use client";
import { useEffect, useState } from "react";
import { Empty, ErrorAlert } from "@/components/ui";
import { api } from "@/lib/api";
import { downloadUrl, isDesktop } from "@/lib/desktop";
import { ago, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { ExportJob } from "@/lib/types";

export default function Exports() {
  const { data, reload, error } = useApi<ExportJob[]>("/exports");
  const [err, setErr] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pending = data?.some((e) => ["pending", "running"].includes(e.export_status));
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(reload, 2500);
    return () => clearInterval(t);
  }, [pending, reload]);

  async function download(e: ExportJob) {
    try {
      const { url } = await api.post<{ url: string }>(`/exports/${e.id}/link`);
      const saved = await downloadUrl(url, `product-export-${e.created_at.slice(0, 10)}.${e.export_format}`);
      if (saved) setNotice(`Saved to ${saved}`);
    } catch (x) { setErr(x); }
  }
  async function create(format: string) {
    try { await api.post("/exports", { export_format: format }); reload(); } catch (x) { setErr(x); }
  }
  return (
    <div className="stack">
      <div className="page-head">
        <div><h1>Exports</h1><p>Excel workbooks (Products, Variations, Images, Price History, Metrics, Profit Analysis + manifest) and CSV. Files expire automatically.</p></div>
        <div className="row"><button onClick={() => create("xlsx")}>Export all (Excel)</button><button onClick={() => create("csv")}>Export all (CSV)</button></div>
      </div>
      {notice && <div className="alert info">{notice}</div>}
      <ErrorAlert error={error ?? err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Created</th><th>Format</th><th>Status</th><th className="num">Rows</th><th className="num">Size</th><th>Expires</th><th /></tr></thead>
          <tbody>
            {data?.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.export_format.toUpperCase()}{e.includes_own_account_data && <span className="badge info" style={{ marginLeft: 6 }}>own-account data</span>}</td>
                <td><span className={`badge ${e.export_status === "completed" ? "good" : e.export_status === "failed" ? "bad" : "neutral"}`}>{e.export_status}</span>
                  {e.error_summary && <div className="small muted">{e.error_summary}</div>}</td>
                <td className="num">{num(e.row_count)}</td>
                <td className="num">{e.file_size_bytes ? `${(e.file_size_bytes / 1024).toFixed(0)} KB` : "—"}</td>
                <td className="small muted">{e.expires_at ? new Date(e.expires_at).toLocaleDateString() : "—"}</td>
                <td>{e.export_status === "completed" && <button onClick={() => download(e)}>{isDesktop() ? "Save to Downloads" : "Download"}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.length && <Empty>No exports yet. Use “Export results” on the Product Database to export a filtered set.</Empty>}
      </div>
    </div>
  );
}
