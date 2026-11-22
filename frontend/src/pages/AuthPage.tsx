import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, auth, type User } from "../api";

export default function AuthPage({ mode, onAuth }: { mode: "login" | "signup"; onAuth: (u: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "signup")
        await api("/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });
      const form = new URLSearchParams({ username: email, password });
      const { access_token } = await api<{ access_token: string }>("/auth/login", { method: "POST", body: form });
      auth.set(access_token);
      onAuth(await api<User>("/auth/me")); // App then routes /signup -> Documents, anything else -> Ask
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-8">
        <div>
          <h1 className="text-xl font-semibold text-brand-900">DocAnswer</h1>
          <p className="mt-1 text-sm text-stone-500">Ask questions answered only from your own documents.</p>
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="password">Password</label>
          <input id="password" className="input" type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} required minLength={mode === "signup" ? 8 : 1}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "signup" && <p className="text-xs text-stone-400">At least 8 characters.</p>}
        </div>
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
        <p className="text-center text-sm text-stone-500">
          {mode === "login" ? <>No account? <Link className="text-brand-700 underline" to="/signup">Sign up</Link></>
            : <>Have an account? <Link className="text-brand-700 underline" to="/login">Log in</Link></>}
        </p>
      </form>
    </div>
  );
}
