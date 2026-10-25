"use client";
import { useMemo, useRef, useState } from "react";

type Point = { t: number; v: number };

/** Single-series line chart with crosshair tooltip. One series, one axis: the title
 * names it, so no legend box is needed. */
export function LineChart({
  points, format, height = 220, stepped = false, invert = false, ariaLabel,
}: {
  points: Point[]; format: (v: number) => string; height?: number; stepped?: boolean; invert?: boolean;
  ariaLabel: string;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 720;
  const pad = { l: 64, r: 16, t: 12, b: 28 };
  const geo = useMemo(() => {
    if (!points.length) return null;
    const ts = points.map((p) => p.t);
    const vs = points.map((p) => p.v);
    let lo = Math.min(...vs), hi = Math.max(...vs);
    if (lo === hi) { lo -= Math.abs(lo) * 0.05 || 1; hi += Math.abs(hi) * 0.05 || 1; }
    const span = hi - lo;
    lo -= span * 0.08; hi += span * 0.08;
    const t0 = Math.min(...ts), t1 = Math.max(...ts) === t0 ? t0 + 1 : Math.max(...ts);
    const x = (t: number) => pad.l + ((t - t0) / (t1 - t0)) * (width - pad.l - pad.r);
    const y = (v: number) => {
      const r = (v - lo) / (hi - lo);
      return pad.t + (invert ? r : 1 - r) * (height - pad.t - pad.b);
    };
    const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3);
    let d = "";
    points.forEach((p, i) => {
      if (i === 0) d = `M${x(p.t)},${y(p.v)}`;
      else if (stepped) d += ` H${x(p.t)} V${y(p.v)}`;
      else d += ` L${x(p.t)},${y(p.v)}`;
    });
    return { x, y, ticks, d, t0, t1 };
  }, [points, height, stepped, invert]);

  if (!geo) return <div className="empty">No observations yet</div>;
  if (points.length === 1) {
    return (
      <div className="empty">
        One observation so far: <strong>{format(points[0].v)}</strong> on {new Date(points[0].t).toLocaleString()}.
        The trend appears once the value is observed again.
      </div>
    );
  }
  const hp = hover !== null ? points[hover] : null;

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    let best = 0, bestD = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(geo!.x(p.t) - px);
      if (d < bestD) { bestD = d; best = i; }
    });
    setHover(best);
  }

  const dateFmt = (t: number) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div className="chart">
      <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {geo.ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} stroke="var(--grid)" strokeWidth={1} />
            <text x={pad.l - 8} y={geo.y(v) + 4} textAnchor="end" fontSize={11} fill="var(--text-muted)">{format(v)}</text>
          </g>
        ))}
        <text x={pad.l} y={height - 6} fontSize={11} fill="var(--text-muted)">{dateFmt(geo.t0)}</text>
        <text x={width - pad.r} y={height - 6} fontSize={11} textAnchor="end" fill="var(--text-muted)">{dateFmt(geo.t1)}</text>
        <path d={geo.d} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.length <= 40 && points.map((p) => (
          <circle key={p.t} cx={geo.x(p.t)} cy={geo.y(p.v)} r={3} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
        ))}
        {hp && (
          <g>
            <line x1={geo.x(hp.t)} x2={geo.x(hp.t)} y1={pad.t} y2={height - pad.b} stroke="var(--text-muted)" strokeDasharray="3 3" />
            <circle cx={geo.x(hp.t)} cy={geo.y(hp.v)} r={5} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hp && (
        <div className="tooltip" style={{
          left: `${(geo.x(hp.t) / width) * 100}%`, top: 0,
          transform: geo.x(hp.t) > width * 0.7 ? "translateX(-105%)" : "translateX(8px)",
        }}>
          <div className="muted">{new Date(hp.t).toLocaleString()}</div>
          <strong>{format(hp.v)}</strong>
        </div>
      )}
    </div>
  );
}

/** Horizontal bar list: magnitude by category, labels and values in text ink. */
export function BarList({ rows, format = (n) => n.toLocaleString() }: {
  rows: { label: string; value: number; title?: string }[]; format?: (n: number) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <div className="empty">No data yet</div>;
  return (
    <div className="barlist">
      {rows.map((r) => (
        <div className="bar-row" key={r.label} title={r.title ?? `${r.label}: ${format(r.value)}`}>
          <span className="secondary" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.label}</span>
          <div className="track"><div className="fill" style={{ width: `${(r.value / max) * 100}%` }} /></div>
          <span className="right" style={{ fontVariantNumeric: "tabular-nums" }}>{format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
