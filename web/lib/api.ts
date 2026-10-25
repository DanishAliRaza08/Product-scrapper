"use client";
import { desktopApiBase, isDesktop, loadRefreshToken, saveRefreshToken } from "./desktop";
import type { Session } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

let accessToken: string | null = null;
let session: Session | null = null;
let base: string | null = null;
const listeners = new Set<(s: Session | null) => void>();

async function apiBase(): Promise<string> {
  if (base) return base;
  base = (await desktopApiBase()) ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return base;
}

export function onSession(fn: (s: Session | null) => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(s: Session | null) {
  session = s;
  accessToken = s?.access_token ?? null;
  listeners.forEach((fn) => fn(s));
}

export const currentSession = () => session;
export const can = (permission: string) => !!session?.permissions.includes(permission);

export async function login(email: string, password: string): Promise<Session> {
  const s = await request<Session>("POST", "/auth/login", {
    email, password, client_type: isDesktop() ? "desktop" : "web",
  }, { auth: false });
  if (s.refresh_token) await saveRefreshToken(s.refresh_token);
  setSession(s);
  return s;
}

export async function register(organisation_name: string, email: string, password: string): Promise<Session> {
  const s = await request<Session>("POST", "/auth/register", { organisation_name, email, password }, { auth: false });
  setSession(s);
  return s;
}

let refreshing: Promise<boolean> | null = null;

/** Rotate the refresh token (httpOnly cookie on web, OS keychain on desktop). */
export function refresh(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const stored = await loadRefreshToken();
      const s = await request<Session>("POST", "/auth/refresh", stored ? { refresh_token: stored } : {}, {
        auth: false, retry: false,
      });
      if (s.refresh_token) await saveRefreshToken(s.refresh_token);
      setSession(s);
      return true;
    } catch {
      setSession(null);
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function logout() {
  const stored = await loadRefreshToken();
  await request("POST", "/auth/logout", stored ? { refresh_token: stored } : {}, { auth: false }).catch(() => undefined);
  await saveRefreshToken(null);
  setSession(null);
}

type Options = { auth?: boolean; retry?: boolean; headers?: Record<string, string> };

export async function request<T>(method: string, path: string, body?: unknown, opts: Options = {}): Promise<T> {
  const { auth = true, retry = true } = opts;
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const res = await fetch(`${await apiBase()}/api/v1${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "include",
  });
  if (res.status === 401 && auth && retry && (await refresh())) {
    return request<T>(method, path, body, { ...opts, retry: false });
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(res.status, err.code ?? "http_error", err.message ?? res.statusText, err.details);
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>("POST", path, body ?? {}, { headers }),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | string[] | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
