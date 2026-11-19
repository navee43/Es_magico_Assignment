import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2, Flag, Plus, Save, Trash2, Undo2 } from "lucide-react";
import { useDocument, useDocuments, useResult, useReview } from "../hooks/useApi";
import { errorMessage, type CorrectedInvoice } from "../services/api";
import { DocumentViewer } from "../components/DocumentViewer";
import { REASON_FIELD, ReconciliationTape, Section } from "../components/ExtractionPanel";
import { DocumentStatus, ErrorNotice, Field, Spinner } from "../components/ui";
import type { InvoiceResult, ReviewIssue } from "../types/api";

interface DraftItem {
  description: string;
  quantity: string;
  amount: string;
}
interface Draft {
  is_invoice: boolean;
  vendor: string;
  invoice_number: string;
  invoice_date: string;
  currency: string;
  tax_total: string;
  total: string;
  line_items: DraftItem[];
}

const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));

function toDraft(inv: InvoiceResult | null): Draft {
  return {
    is_invoice: inv?.is_invoice ?? true,
    vendor: str(inv?.vendor),
    invoice_number: str(inv?.invoice_number),
    invoice_date: str(inv?.invoice_date),
    currency: str(inv?.currency),
    tax_total: str(inv?.tax_total),
    total: str(inv?.total),
    line_items: (inv?.line_items ?? []).map((i) => ({ description: str(i.description), quantity: str(i.quantity), amount: str(i.amount) })),
  };
}

function parseNum(value: string): number | null | "invalid" {
  const t = value.replace(/,/g, "").trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : "invalid";
}

function fromDraft(d: Draft): { value: CorrectedInvoice | null; errors: string[] } {
  const errors: string[] = [];
  const num = (label: string, v: string) => {
    const n = parseNum(v);
    if (n === "invalid") {
      errors.push(`${label} must be a number`);
      return null;
    }
    return n;
  };
  if (!d.is_invoice) {
    return { value: { is_invoice: false, vendor: null, invoice_number: null, invoice_date: null, currency: null, line_items: null, tax_total: null, total: null }, errors };
  }
  if (d.invoice_date && !/^\d{4}-\d{2}-\d{2}$/.test(d.invoice_date)) errors.push("Invoice date must be YYYY-MM-DD");
  if (d.currency && !/^[A-Za-z]{3}$/.test(d.currency.trim())) errors.push("Currency must be a 3-letter ISO code such as INR");
  const items = d.line_items
    .filter((i) => i.description.trim() || i.quantity.trim() || i.amount.trim())
    .map((i, idx) => ({
      description: i.description.trim() || null,
      quantity: num(`Line ${idx + 1} quantity`, i.quantity),
      amount: num(`Line ${idx + 1} amount`, i.amount),
    }));
  return {
    value: {
      is_invoice: true,
      vendor: d.vendor.trim() || null,
      invoice_number: d.invoice_number.trim() || null,
      invoice_date: d.invoice_date || null,
      currency: d.currency.trim().toUpperCase() || null,
      line_items: items.length ? items : null,
      tax_total: num("Tax", d.tax_total),
      total: num("Total", d.total),
    },
    errors,
  };
}

function FlagNote({ issues }: { issues: ReviewIssue[] }) {
  return (
    <>
      {issues.map((i) => (
        <span key={i.code + i.message} className="mt-1 flex items-start gap-1 text-xs text-audit">
          <Flag className="mt-0.5 size-3 shrink-0" aria-hidden /> {i.message}
        </span>
      ))}
    </>
  );
}

export function ReviewEditorPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const doc = useDocument(id);
  const result = useResult(id);
  const review = useReview(id);
  const queue = useDocuments({ review: "pending", limit: 200 });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  const original = result.data?.model_output ?? null;
  const startingPoint = result.data?.corrected ?? original;

  useEffect(() => {
    if (result.data && draft === null) {
      setDraft(toDraft(startingPoint));
      setNote(result.data.review_note ?? "");
    }
  }, [result.data, draft, startingPoint]);

  const originalDraft = useMemo(() => toDraft(original), [original]);
  const parsed = useMemo(() => (draft ? fromDraft(draft) : { value: null, errors: [] }), [draft]);
  const issues = (result.data?.review_details ?? []).filter((i) => i.severity === "review");
  const flagsFor = (field: keyof InvoiceResult) => issues.filter((i) => REASON_FIELD[i.code] === field);
  const docLevel = issues.filter((i) => !REASON_FIELD[i.code]);
  const nextId = queue.data?.items.find((d) => d.id !== id)?.id;

  if (doc.isLoading || result.isLoading || !draft) {
    if (doc.isError || result.isError) return <ErrorNotice title="Couldn't open this document" message={errorMessage(doc.error ?? result.error)} />;
    return <Spinner label="Loading review" />;
  }
  const d = doc.data!;
  const changed = JSON.stringify(draft) !== JSON.stringify(originalDraft);

  const set = (patch: Partial<Draft>) => {
    setSaved(null);
    setDraft((cur) => (cur ? { ...cur, ...patch } : cur));
  };
  const setItem = (idx: number, patch: Partial<DraftItem>) =>
    set({ line_items: draft.line_items.map((item, i) => (i === idx ? { ...item, ...patch } : item)) });

  const textInput = (key: "vendor" | "invoice_number" | "invoice_date" | "currency" | "tax_total" | "total", props: Record<string, unknown> = {}) => {
    const isChanged = draft[key] !== originalDraft[key];
    return (
      <>
        <input
          className={`input ${isChanged ? "input-changed" : ""} ${["tax_total", "total"].includes(key) ? "num text-right" : ""}`}
          value={draft[key]}
          onChange={(e) => set({ [key]: e.target.value } as Partial<Draft>)}
          {...props}
        />
        {isChanged && original && <span className="mt-1 block text-xs text-muted">Model: {originalDraft[key] || "empty"}</span>}
        {!isChanged && <FlagNote issues={flagsFor(key as keyof InvoiceResult)} />}
      </>
    );
  };

  async function submit(action: "save" | "approve") {
    if (parsed.errors.length) return;
    await review.mutateAsync({ action, corrected: changed || !original ? parsed.value : null, note: note || null });
    if (action === "approve") navigate(nextId ? `/review/${nextId}` : "/review");
    else setSaved("Draft saved. The document stays in the review queue until you approve it.");
  }

  const preview = parsed.value;

  return (
    <>
      <div className="mb-5">
        <Link to="/review" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Review queue
        </Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-[24px] font-semibold leading-tight">Review {d.original_filename}</h1>
            <div className="mt-2 flex items-center gap-2"><DocumentStatus doc={d} /> <Link to={`/documents/${id}`} className="text-sm text-ledger hover:underline">View extraction details</Link></div>
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className="xl:sticky xl:top-6 xl:h-[calc(100vh-3rem)]">
          <DocumentViewer id={id} hasFile={d.has_file} mimeType={d.mime_type} />
        </div>

        <div className="space-y-5">
          {docLevel.length > 0 && (
            <section className="rounded-lg border border-audit/30 bg-audit-soft px-5 py-3 text-sm">
              {docLevel.map((i) => (
                <p key={i.code + i.message} className="flex items-start gap-2 text-ink-soft">
                  <Flag className="mt-0.5 size-4 shrink-0 text-audit" aria-hidden /> {i.message}
                </p>
              ))}
            </section>
          )}
          {!original && (
            <ErrorNotice title="No model output to start from" message="Processing failed, so enter the values from the document yourself. The original failure stays on record." />
          )}

          <Section
            title="Fields"
            aside={
              changed && original ? (
                <button className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink" onClick={() => set(originalDraft)}>
                  <Undo2 className="size-3.5" aria-hidden /> Reset to model output
                </button>
              ) : undefined
            }
          >
            <fieldset className="mb-4">
              <legend className="mb-1 text-[13px] font-medium text-ink-soft">Document type</legend>
              <div className="inline-flex rounded-md border border-rule p-0.5">
                {[true, false].map((v) => (
                  <button
                    key={String(v)}
                    type="button"
                    onClick={() => set({ is_invoice: v })}
                    aria-pressed={draft.is_invoice === v}
                    className={`rounded px-3 py-1 text-sm font-medium ${draft.is_invoice === v ? "bg-ink text-white" : "text-ink-soft hover:bg-rule-soft"}`}
                  >
                    {v ? "Invoice" : "Not an invoice"}
                  </button>
                ))}
              </div>
            </fieldset>

            {draft.is_invoice ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Vendor">{textInput("vendor")}</Field>
                <Field label="Invoice number">{textInput("invoice_number")}</Field>
                <Field label="Invoice date">{textInput("invoice_date", { type: "date" })}</Field>
                <Field label="Currency" hint="ISO code, e.g. INR, USD, EUR">{textInput("currency", { maxLength: 3 })}</Field>
                <Field label="Tax total">{textInput("tax_total", { inputMode: "decimal" })}</Field>
                <Field label="Total">{textInput("total", { inputMode: "decimal" })}</Field>
              </div>
            ) : (
              <p className="text-sm text-muted">All invoice fields will be saved as null.</p>
            )}
          </Section>

          {draft.is_invoice && (
            <Section title={`Line items (${draft.line_items.length})`}>
              <FlagNote issues={flagsFor("line_items")} />
              <div className="overflow-x-auto">
                <table className="mt-1 w-full text-sm">
                  <thead>
                    <tr className="text-left text-[13px] text-muted">
                      <th className="pb-1.5 pr-2 font-medium">Description</th>
                      <th className="w-20 pb-1.5 pr-2 text-right font-medium">Qty</th>
                      <th className="w-32 pb-1.5 pr-2 text-right font-medium">Amount</th>
                      <th className="w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {draft.line_items.map((item, idx) => {
                      const o = originalDraft.line_items[idx];
                      return (
                        <tr key={idx}>
                          <td className="py-1 pr-2">
                            <input aria-label={`Line ${idx + 1} description`} className={`input ${o?.description !== item.description ? "input-changed" : ""}`} value={item.description} onChange={(e) => setItem(idx, { description: e.target.value })} />
                          </td>
                          <td className="py-1 pr-2">
                            <input aria-label={`Line ${idx + 1} quantity`} inputMode="decimal" className={`input num text-right ${o?.quantity !== item.quantity ? "input-changed" : ""}`} value={item.quantity} onChange={(e) => setItem(idx, { quantity: e.target.value })} />
                          </td>
                          <td className="py-1 pr-2">
                            <input aria-label={`Line ${idx + 1} amount`} inputMode="decimal" className={`input num text-right ${o?.amount !== item.amount ? "input-changed" : ""}`} value={item.amount} onChange={(e) => setItem(idx, { amount: e.target.value })} />
                          </td>
                          <td className="py-1">
                            <button className="rounded p-1.5 text-muted hover:bg-fail-soft hover:text-fail" aria-label={`Remove line ${idx + 1}`} onClick={() => set({ line_items: draft.line_items.filter((_, i) => i !== idx) })}>
                              <Trash2 className="size-4" />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <button className="btn btn-quiet mt-3" onClick={() => set({ line_items: [...draft.line_items, { description: "", quantity: "", amount: "" }] })}>
                <Plus className="size-4" aria-hidden /> Add line
              </button>
            </Section>
          )}

          {draft.is_invoice && preview && (
            <Section title="Does it add up now?">
              <ReconciliationTape items={preview.line_items} tax={preview.tax_total} total={preview.total} currency={preview.currency} extras={result.data?.diagnostics.extras} />
            </Section>
          )}

          <Section title="Reviewer note">
            <textarea className="input min-h-20" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional: what you changed and why" />
          </Section>

          {parsed.errors.length > 0 && <ErrorNotice title="Fix these before saving" message={parsed.errors.join(". ") + "."} />}
          {review.isError && <ErrorNotice title="Couldn't save the review" message={errorMessage(review.error)} />}
          {saved && <p className="flex items-center gap-2 text-sm text-ledger" role="status"><CheckCircle2 className="size-4" aria-hidden /> {saved}</p>}

          <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center justify-end gap-2 border-t border-rule bg-paper/95 px-1 py-3 backdrop-blur">
            <span className="w-full text-xs text-muted">
              {changed ? "Your corrections are stored separately; the model's original output is kept." : original ? "No changes: approving accepts the model output." : ""}
            </span>
            <button className="btn btn-quiet" disabled={review.isPending || parsed.errors.length > 0} onClick={() => submit("save")}>
              <Save className="size-4" aria-hidden /> Save draft
            </button>
            <button className="btn btn-approve" disabled={review.isPending || parsed.errors.length > 0} onClick={() => submit("approve")}>
              <CheckCircle2 className="size-4" aria-hidden /> {nextId ? "Approve and open next" : "Approve"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
