"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { login, register } from "@/lib/api";
import { ErrorAlert } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [org, setOrg] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(org, email, password);
      router.replace("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="card auth-card stack" onSubmit={submit}>
        <div>
          <h1>{mode === "login" ? "Sign in" : "Create organisation"}</h1>
          <p className="secondary">Multi-marketplace product intelligence</p>
        </div>
        <ErrorAlert error={error} />
        {mode === "register" && (
          <label className="field"><span>Organisation name</span>
            <input required minLength={2} value={org} onChange={(e) => setOrg(e.target.value)} />
          </label>
        )}
        <label className="field"><span>Email</span>
          <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field"><span>Password</span>
          <input type="password" required minLength={mode === "register" ? 12 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "register" && <span className="muted">At least 12 characters.</span>}
        </label>
        <button className="primary" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New here? Create an organisation" : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
