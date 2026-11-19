import { CheckCircle2, CircleSlash, Flag, Info, XCircle } from "lucide-react";
import type { DocumentResult, InvoiceResult, ReviewIssue } from "../types/api";
import { ROUTE_LABELS, count, duration, humanize, longDate, money, usd } from "../lib/format";
import { ReconciliationTape } from "./ReconciliationTape";

/** Which review reasons concern which field, so flags sit next to the value they question. */
export const REASON_FIELD: Record<string, keyof InvoiceResult> = {
  MISSING_VENDOR: "vendor",
  UNGROUNDED_VENDOR: "vendor",
  MISSING_INVOICE_NUMBER: "invoice_number",
  UNGROUNDED_INVOICE_NUMBER: "invoice_number",
  MISSING_INVOICE_DATE: "invoice_date",
  INVALID_DATE: "invoice_date",
  AMBIGUOUS_DATE: "invoice_date",
  DATE_INCONSISTENT: "invoice_date",
  MISSING_CURRENCY: "currency",
  INVALID_CURRENCY: "currency",
  MISSING_TOTAL: "total",
  UNGROUNDED_TOTAL: "total",
  TOTAL_MISMATCH: "total",
  MISSING_LINE_ITEMS: "line_items",
  INCOMPLETE_LINE_ITEMS: "line_items",
};

export function issuesFor(field: keyof InvoiceResult, issues: ReviewIssue[]) {
  return issues.filter((i) => i.severity === "review" && REASON_FIELD[i.code] === field);
}

function FieldLine({ label, value, issues }: { label: string; value: React.ReactNode; issues: ReviewIssue[] }) {
  const flagged = issues.length > 0;
  return (
    <div className={`grid grid-cols-[9.5rem_1fr] gap-3 border-b border-rule-soft py-2.5 last:border-0 ${flagged ? "relative" : ""}`}>
      <dt className="text-[13.5px] text-muted">{label}</dt>
      <dd className="min-w-0">
        <div className={`font-medium ${value === null ? "text-muted" : ""}`}>{value ?? "Not found"}</div>
        {issues.map((issue) => (
          <p key={issue.code + issue.message} className="mt-1 flex items-start gap-1.5 text-[13px] text-audit">
            <Flag className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span>{issue.message}</span>
          </p>
        ))}
      </dd>
    </div>
  );
}

export function InvoiceFields({ invoice, issues }: { invoice: InvoiceResult; issues: ReviewIssue[] }) {
  return (
    <dl>
      <FieldLine label="Vendor" value={invoice.vendor} issues={issuesFor("vendor", issues)} />
      <FieldLine label="Invoice number" value={invoice.invoice_number} issues={issuesFor("invoice_number", issues)} />
      <FieldLine
        label="Invoice date"
        value={invoice.invoice_date ? <span className="num">{longDate(invoice.invoice_date)} <span className="text-muted">({invoice.invoice_date})</span></span> : null}
        issues={issuesFor("invoice_date", issues)}
      />
      <FieldLine label="Currency" value={invoice.currency} issues={issuesFor("currency", issues)} />
      <FieldLine label="Tax" value={invoice.tax_total === null ? null : <span className="num">{money(invoice.tax_total, invoice.currency)}</span>} issues={[]} />
      <FieldLine label="Total" value={invoice.total === null ? null : <span className="num text-lg font-semibold">{money(invoice.total, invoice.currency)}</span>} issues={issuesFor("total", issues)} />
    </dl>
  );
}

export function LineItemsTable({ invoice, issues }: { invoice: InvoiceResult; issues: ReviewIssue[] }) {
  const rows = invoice.line_items ?? [];
  const flags = issuesFor("line_items", issues);
  return (
    <div>
      {flags.map((f) => (
        <p key={f.code} className="mb-2 flex items-start gap-1.5 text-[13px] text-audit">
          <Flag className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {f.message}
        </p>
      ))}
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No line items were extracted.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-rule text-left text-[13px] text-muted">
                <th className="py-2 pr-3 font-medium">Description</th>
                <th className="py-2 pr-3 text-right font-medium">Qty</th>
                <th className="py-2 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item, i) => (
                <tr key={i} className="border-b border-rule-soft last:border-0">
                  <td className="py-2 pr-3">{item.description ?? <span className="text-muted">—</span>}</td>
                  <td className="num py-2 pr-3 text-right">{item.quantity ?? "—"}</td>
                  <td className="num py-2 text-right">{money(item.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="panel p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Check({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  const Icon = ok === null ? Info : ok ? CheckCircle2 : XCircle;
  const tone = ok === null ? "text-muted" : ok ? "text-ledger" : "text-audit";
  return (
    <li className="flex items-start gap-2 py-1.5">
      <Icon className={`mt-0.5 size-4 shrink-0 ${tone}`} aria-hidden />
      <div>
        <span className="font-medium">{label}</span>
        {detail && <span className="block text-[13px] text-muted">{detail}</span>}
      </div>
    </li>
  );
}

export function ValidationList({ result }: { result: DocumentResult }) {
  const d = result.diagnostics;
  const fin = d.validation?.financial;
  const g = d.validation?.grounding ?? {};
  const finOk = fin?.status === "pass" || fin?.status === "pass_with_adjustments" ? true : fin?.status === "mismatch" ? false : null;
  const groundingKeys = ["invoice_number_found", "total_found", "vendor_found"].filter((k) => k in g);
  const groundingOk = groundingKeys.length ? groundingKeys.every((k) => g[k]) : null;
  return (
    <ul className="text-sm">
      <Check ok={d.validation?.schema_valid ?? false} label="Schema" detail={d.validation?.schema_valid_first_try === false ? "Valid after one repair request" : "Output matches the required JSON schema"} />
      <Check ok={finOk} label="Arithmetic" detail={fin?.explanation ?? fin?.message ?? (finOk ? "Line items + tax = total" : undefined)} />
      <Check
        ok={groundingOk}
        label="Values found in the source text"
        detail={
          groundingKeys.length
            ? groundingKeys.map((k) => `${humanize(k.replace("_found", ""))}: ${g[k] ? "found" : "not found"}`).join(", ")
            : "No source text to compare against"
        }
      />
    </ul>
  );
}

function ocrLabel(s: DocumentResult["diagnostics"]["source"] | undefined): string {
  if (!s) return "—";
  if (s.ocr_mean_confidence != null) return `${s.ocr_mean_confidence} (${s.ocr_word_count} words)`;
  if (s.ocr_error?.startsWith("TESSERACT_UNAVAILABLE")) return "unavailable: Tesseract not installed";
  if (s.ocr_error?.startsWith("OCR_DISABLED")) return "disabled";
  if (s.ocr_error) return `failed (${s.ocr_error.split(":")[0]})`;
  if (s.ocr_pages > 0) return "no text found";
  return "not needed";
}

export function ProcessingDetails({ result }: { result: DocumentResult }) {
  const d = result.diagnostics;
  const s = d.source;
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
        <div><dt className="text-muted">Input sent to model</dt><dd className="font-medium">{ROUTE_LABELS[d.route] ?? d.route}</dd></div>
        <div><dt className="text-muted">Pages</dt><dd className="num font-medium">{s ? `${s.pages_processed} of ${s.pages_total}` : "—"}</dd></div>
        <div><dt className="text-muted">OCR confidence</dt><dd className="num font-medium">{ocrLabel(s)}</dd></div>
        <div><dt className="text-muted">Tokens</dt><dd className="num font-medium">{count(d.usage?.input_tokens)} in, {count(d.usage?.output_tokens)} out</dd></div>
        <div><dt className="text-muted">Model cost</dt><dd className="num font-medium">{d.cached ? `${usd(d.original_cost_usd)} (cached, $0 now)` : usd(d.usage?.cost_usd, 5)}</dd></div>
        <div><dt className="text-muted">Processing time</dt><dd className="num font-medium">{duration(d.processing_ms)}</dd></div>
      </dl>
      {d.route_reason && <p className="text-muted">Why this input: {d.route_reason}.</p>}
      {d.attempts?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-rule text-left text-muted">
                <th className="py-1.5 pr-3 font-medium">Call</th>
                <th className="py-1.5 pr-3 font-medium">Purpose</th>
                <th className="py-1.5 pr-3 font-medium">Model</th>
                <th className="py-1.5 pr-3 text-right font-medium">Tokens in / out</th>
                <th className="py-1.5 pr-3 text-right font-medium">Cost</th>
                <th className="py-1.5 text-right font-medium">Time</th>
              </tr>
            </thead>
            <tbody className="num">
              {d.attempts.map((a) => (
                <tr key={a.number} className="border-b border-rule-soft last:border-0">
                  <td className="py-1.5 pr-3">{a.number}</td>
                  <td className="py-1.5 pr-3">{humanize(a.purpose)}{a.error ? ` (${a.error})` : ""}</td>
                  <td className="py-1.5 pr-3">{a.model}</td>
                  <td className="py-1.5 pr-3 text-right">{count(a.input_tokens)} / {count(a.output_tokens)}</td>
                  <td className="py-1.5 pr-3 text-right">{usd(a.cost_usd, 5)}</td>
                  <td className="py-1.5 text-right">{duration(a.latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function NonInvoiceNotice({ docType }: { docType: string | null }) {
  return (
    <div className="flex items-start gap-3 rounded-lg bg-rule-soft p-4">
      <CircleSlash className="mt-0.5 size-5 text-ink-soft" aria-hidden />
      <div>
        <p className="font-semibold">Not an invoice</p>
        <p className="text-sm text-muted">
          The model classified this as {docType && docType !== "other" ? `a ${humanize(docType).toLowerCase()}` : "a non-invoice document"}, so every invoice field is null.
        </p>
      </div>
    </div>
  );
}

export { ReconciliationTape };
