/** Bridge to the Tauri desktop shell. In the browser every function is a no-op, so
 * the same UI runs in both clients. Secrets never live in the frontend bundle: the
 * desktop refresh token is kept in the OS keychain by the Rust side. */
type TauriInternals = { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };

function tauri(): TauriInternals | null {
  if (typeof window === "undefined") return null;
  return ((window as unknown as { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__) ?? null;
}

export const isDesktop = () => tauri() !== null;

export async function saveRefreshToken(token: string | null): Promise<void> {
  const t = tauri();
  if (!t) return;
  await t.invoke(token ? "store_refresh_token" : "clear_refresh_token", token ? { token } : {});
}

export async function loadRefreshToken(): Promise<string | null> {
  const t = tauri();
  if (!t) return null;
  return ((await t.invoke("load_refresh_token")) as string | null) ?? null;
}

export async function desktopApiBase(): Promise<string | null> {
  const t = tauri();
  if (!t) return null;
  return ((await t.invoke("api_base_url")) as string | null) ?? null;
}

export async function notify(title: string, body: string): Promise<void> {
  const t = tauri();
  if (t) {
    await t.invoke("notify", { title, body }).catch(() => undefined);
  } else if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

/** Download a file. On desktop it is saved straight to the user's Downloads folder. */
export async function downloadUrl(url: string, filename: string): Promise<string | null> {
  const t = tauri();
  if (t) return (await t.invoke("download_file", { url, filename })) as string;
  window.location.href = url;
  return null;
}
