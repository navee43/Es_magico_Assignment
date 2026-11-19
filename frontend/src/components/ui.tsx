import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, CircleSlash, Clock, Flag, Loader2, XCircle } from "lucide-react";
import type { DocumentSummary } from "../types/api";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span className="text-sm">{label}…</span>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.01em]">{title}</h1>
        {description && <p className="mt-1 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function ErrorNotice({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-fail/30 bg-fail-soft px-4 py-3" role="alert">
      <div className="flex items-start gap-3">
        <XCircle className="mt-0.5 size-5 shrink-0 text-fail" aria-hidden />
        <div className="min-w-0">
          <p className="font-semibold text-fail">{title}</p>
          <p className="mt-0.5 text-sm text-ink-soft">{message}</p>
          {action && <div className="mt-3">{action}</div>}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <div className="mb-3 text-muted">{icon}</div>
      <p className="font-semibold">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

type Tone = "ledger" | "audit" | "fail" | "neutral" | "ink";

const TONES: Record<Tone, string> = {
  ledger: "bg-ledger-soft text-ledger",
  audit: "bg-audit-soft text-audit",
  fail: "bg-fail-soft text-fail",
  neutral: "bg-rule-soft text-ink-soft",
  ink: "bg-ink text-white",
};

export function Pill({ tone, icon, children }: { tone: Tone; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[12.5px] font-semibold ${TONES[tone]}`}>
      {icon}
      {children}
    </span>
  );
}

/** One badge that summarises where a document stands. */
export function DocumentStatus({ doc }: { doc: Pick<DocumentSummary, "status" | "is_invoice" | "needs_review" | "review_status"> }) {
  const icon = "size-3.5";
  if (doc.status === "processing") return <Pill tone="neutral" icon={<Clock className={icon} />}>Processing</Pill>;
  if (doc.review_status === "approved") return <Pill tone="ledger" icon={<CheckCircle2 className={icon} />}>Approved</Pill>;
  if (doc.status === "failed") return <Pill tone="fail" icon={<XCircle className={icon} />}>Failed</Pill>;
  if (doc.needs_review) return <Pill tone="audit" icon={<Flag className={icon} />}>Needs review</Pill>;
  if (doc.is_invoice === false) return <Pill tone="neutral" icon={<CircleSlash className={icon} />}>Not an invoice</Pill>;
  return <Pill tone="ledger" icon={<CheckCircle2 className={icon} />}>Passed checks</Pill>;
}

export function MockBanner() {
  return (
    <div className="flex items-center gap-2 border-b border-audit/30 bg-audit-soft px-5 py-2 text-sm text-audit">
      <AlertTriangle className="size-4 shrink-0" aria-hidden />
      <span>
        <strong>Mock model.</strong> Results come from a regex stub for UI testing, not a real LLM. Set <code>LLM_PROVIDER=openai</code> for real extraction.
      </span>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] font-medium text-ink-soft">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}
