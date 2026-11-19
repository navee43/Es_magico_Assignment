import { useState, type FormEvent } from "react";
import { Lock } from "lucide-react";
import { api, errorMessage, tokenStore } from "../services/api";

export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token } = await api.login(password);
      tokenStore.set(token);
      onSignedIn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-ink px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl bg-white p-7 shadow-xl">
        <div className="mb-6 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-9" />
          <div>
            <h1 className="text-lg font-semibold">Invoice AI</h1>
            <p className="text-sm text-muted">Invoices are confidential. Sign in to continue.</p>
          </div>
        </div>
        <label className="block">
          <span className="mb-1 block text-[13px] font-medium text-ink-soft">Password</span>
          <input
            type="password"
            className="input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
        </label>
        {error && (
          <p className="mt-3 text-sm text-fail" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary mt-5 w-full justify-center" disabled={busy || !password}>
          <Lock className="size-4" aria-hidden /> {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
