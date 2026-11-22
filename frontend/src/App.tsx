import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, auth, type Health, type User } from "./api";
import AuthPage from "./pages/AuthPage";
import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import EvaluationPage from "./pages/EvaluationPage";
import MonitoringPage from "./pages/MonitoringPage";

const NAV = [
  ["/chat", "Ask"],
  ["/documents", "Documents"],
  ["/evaluation", "Evaluation"],
  ["/monitoring", "Monitoring"],
] as const;

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api<Health>("/health").then(setHealth).catch(() => setHealth(null));
    if (!auth.token) return setChecked(true);
    api<User>("/auth/me").then(setUser).catch(() => auth.set(null)).finally(() => setChecked(true));
  }, []);

  useEffect(() => {
    const onLogout = () => { setUser(null); navigate("/login"); };
    window.addEventListener("rag:logout", onLogout);
    return () => window.removeEventListener("rag:logout", onLogout);
  }, [navigate]);

  if (!checked) return null;
  if (!user)
    return (
      <Routes>
        <Route path="/signup" element={<AuthPage mode="signup" onAuth={setUser} />} />
        <Route path="*" element={<AuthPage mode="login" onAuth={setUser} />} />
      </Routes>
    );

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <span className="font-semibold text-brand-900">DocAnswer</span>
          <nav className="flex gap-1">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to}
                className={({ isActive }) => `rounded-lg px-3 py-1.5 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-stone-600 hover:bg-stone-100"}`}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-stone-500">
            {health && (
              <span title={Object.entries(health.checks).map(([k, v]) => `${k}: ${v}`).join("\n")}
                className={`rounded-full px-2 py-0.5 text-xs ${health.status === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                {health.mode === "fake" ? "test mode" : health.status}
              </span>
            )}
            <span className="hidden sm:inline">{user.email}</span>
            <button className="btn-ghost" onClick={() => { auth.set(null); setUser(null); navigate("/login"); }}>Log out</button>
          </div>
        </div>
      </header>
      {health?.mode === "fake" && (
        <div className="bg-amber-50 px-4 py-2 text-center text-sm text-amber-800">
          Test mode: answers come from offline fake providers, not a real model.
        </div>
      )}
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/evaluation" element={<EvaluationPage />} />
          <Route path="/monitoring" element={<MonitoringPage />} />
          <Route path="/signup" element={<Navigate to="/documents" replace />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </main>
    </div>
  );
}
