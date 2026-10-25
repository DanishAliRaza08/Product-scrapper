"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, can, currentSession, logout, onSession, refresh } from "@/lib/api";
import { isDesktop } from "@/lib/desktop";
import type { Session } from "@/lib/types";

const NAV: { title: string; items: { href: string; label: string; perm?: string }[] }[] = [
  { title: "Overview", items: [{ href: "/", label: "Dashboard" }, { href: "/opportunities/", label: "Product Opportunities" }] },
  {
    title: "Research",
    items: [
      { href: "/discover/", label: "Product Discovery" },
      { href: "/products/", label: "Product Database" },
      { href: "/compare/", label: "Product Comparison" },
      { href: "/projects/", label: "Projects & Saved" },
    ],
  },
  {
    title: "Operations",
    items: [
      { href: "/jobs/", label: "Extraction Jobs" },
      { href: "/exports/", label: "Exports" },
      { href: "/connections/", label: "Own Seller Accounts", perm: "view_own_account_data" },
      { href: "/profit/", label: "Profit Calculator" },
    ],
  },
  {
    title: "Admin",
    items: [
      { href: "/connectors/", label: "Marketplace Connections" },
      { href: "/settings/", label: "Settings" },
    ],
  },
];

const PUBLIC = ["/login/"];

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(currentSession());
  const [checked, setChecked] = useState(!!currentSession());
  const [unread, setUnread] = useState(0);
  const isPublic = PUBLIC.includes(pathname.endsWith("/") ? pathname : `${pathname}/`);

  useEffect(() => {
    const off = onSession(setSession);
    if (!currentSession()) refresh().finally(() => setChecked(true));
    return () => { off(); };
  }, []);

  useEffect(() => {
    if (checked && !session && !isPublic) router.replace("/login/");
  }, [checked, session, isPublic, router]);

  useEffect(() => {
    if (!session) return;
    const load = () => api.get<{ id: number }[]>("/notifications?unread_only=true&limit=50")
      .then((n) => setUnread(n.length)).catch(() => undefined);
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [session]);

  if (isPublic) return <>{children}</>;
  if (!session) return <div className="auth-wrap muted">Loading…</div>;

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href.replace(/\/$/, "")));
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Product Intelligence
          <small>{session.organisation_name}{isDesktop() ? " · Desktop" : ""}</small>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((g) => (
            <div className="nav-group" key={g.title}>
              <div className="nav-group-title">{g.title}</div>
              {g.items.filter((i) => !i.perm || can(i.perm)).map((i) => (
                <Link key={i.href} href={i.href} className={active(i.href) ? "active" : ""}>{i.label}</Link>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="small muted">Values are labelled actual, estimated or not available. Nothing is invented.</span>
          <div className="row">
            <Link href="/settings/?tab=Notifications" className="btn" aria-label={`${unread} unread notifications`}>
              Notifications{unread ? <span className="badge info">{unread}</span> : null}
            </Link>
            <span className="small secondary">{session.user.email} · {session.user.role.replace("_", " ")}</span>
            <button onClick={() => logout().then(() => router.replace("/login/"))}>Sign out</button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
