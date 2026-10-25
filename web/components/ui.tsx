"use client";
import type { ReactNode } from "react";
import { label } from "@/lib/format";

export function MeasurementBadge({ type }: { type: string | null | undefined }) {
  const t = type ?? "unavailable";
  const text = t === "actual" ? "Actual" : t === "estimated" ? "Estimated" : "Not available";
  const title =
    t === "actual" ? "Reported by an authorised source"
      : t === "estimated" ? "Estimate: see methodology and confidence"
        : "The source does not expose this value";
  return <span className={`badge ${t}`} title={title}>{text}</span>;
}

const STOCK: Record<string, string> = { in_stock: "good", low_stock: "warn", out_of_stock: "bad", preorder: "info" };

export function StockBadge({ status }: { status: string | null | undefined }) {
  return <span className={`badge ${STOCK[status ?? ""] ?? "neutral"}`}>{label(status ?? "unknown")}</span>;
}

const SOURCE: Record<string, [string, string]> = {
  own_account: ["info", "Own account"],
  competitor_research: ["neutral", "Competitor"],
  partner_estimate: ["warn", "Partner estimate"],
  user_entered: ["neutral", "User entered"],
  derived: ["neutral", "Derived"],
};

export function SourceBadge({ source }: { source: string }) {
  const [cls, text] = SOURCE[source] ?? ["neutral", label(source)];
  return <span className={`badge ${cls}`}>{text}</span>;
}

const JOB: Record<string, string> = {
  completed: "good", partial: "warn", failed: "bad", running: "info", queued: "neutral", pending: "neutral",
  cancelling: "warn", cancelled: "neutral", paused: "warn",
};

export function JobStatusBadge({ status }: { status: string }) {
  return <span className={`badge ${JOB[status] ?? "neutral"}`}>{label(status)}</span>;
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="alert error" role="alert">{message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Stat({ label: l, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card stat">
      <span className="label">{l}</span>
      <span className="value">{value}</span>
      {hint ? <span className="small muted">{hint}</span> : null}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: readonly T[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t} role="tab" aria-selected={t === value} className={t === value ? "active" : ""} onClick={() => onChange(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

export function Progress({ pct }: { pct: number | null }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct ?? 0} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
    </div>
  );
}

/** Remote product image that collapses to a neutral placeholder if it cannot load. */
export function RemoteImage({ src, alt = "", className, style }: { src: string | null | undefined; alt?: string;
  className?: string; style?: React.CSSProperties }) {
  if (!src) return <div className={className} style={style} />;
  return <img src={src} alt={alt} className={className} style={style} loading="lazy" referrerPolicy="no-referrer"
    onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />;
}
