import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Flag, PencilLine, RotateCcw, XCircle } from "lucide-react";
import { useDocument, useResult, useReview } from "../hooks/useApi";
import { errorMessage } from "../services/api";
import { DocumentViewer } from "../components/DocumentViewer";
import {
  InvoiceFields,
  LineItemsTable,
  NonInvoiceNotice,
  ProcessingDetails,
  REASON_FIELD,
  ReconciliationTape,
  Section,
  ValidationList,
} from "../components/ExtractionPanel";
import { DocumentStatus, ErrorNotice, Pill, Spinner } from "../components/ui";
import { humanize } from "../lib/format";

function download(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function DocumentPage() {
  const { id = "" } = useParams();
  const doc = useDocument(id);
  const result = useResult(id);
  const review = useReview(id);

  if (doc.isLoading) return <Spinner label="Loading document" />;
  if (doc.isError) return <ErrorNotice title="Couldn't open this document" message={errorMessage(doc.error)} action={<Link className="btn btn-quiet" to="/documents">Back to documents</Link>} />;
  const d = doc.data!;
  const r = result.data;
  const invoice = r?.effective ?? null;
  const issues = r?.review_details ?? [];
  const docLevel = issues.filter((i) => i.severity === "review" && !REASON_FIELD[i.code]);
  const stem = d.original_filename.replace(/\.[^.]+$/, "");

  return (
    <>
      <div className="mb-5">
        <Link to="/documents" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Documents
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-[24px] font-semibold leading-tight">{d.original_filename}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <DocumentStatus doc={d} />
              {r?.corrected && <Pill tone="neutral" icon={<PencilLine className="size-3.5" />}>Corrected by reviewer</Pill>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {r?.model_output && (
              <button className="btn btn-quiet" onClick={() => download(`${stem}.json`, r.effective)}>
                <Download className="size-4" aria-hidden /> Download JSON
              </button>
            )}
            {r && (
              <button className="btn btn-quiet" onClick={() => download(`${stem}.diagnostics.json`, { result: r.model_output, corrected: r.corrected, review_reasons: r.review_reasons, diagnostics: r.diagnostics })}>
                <Download className="size-4" aria-hidden /> Diagnostics
              </button>
            )}
            {d.review_status === "approved" ? (
              <button className="btn btn-quiet" disabled={review.isPending} onClick={() => review.mutate({ action: "reopen" })}>
                <RotateCcw className="size-4" aria-hidden /> Reopen review
              </button>
            ) : (
              d.status !== "processing" && (
                <Link to={`/review/${id}`} className={d.needs_review ? "btn btn-primary" : "btn btn-quiet"}>
                  <PencilLine className="size-4" aria-hidden /> {d.needs_review ? "Review and correct" : "Edit values"}
                </Link>
              )
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className="xl:sticky xl:top-6 xl:h-[calc(100vh-3rem)]">
          <DocumentViewer id={id} hasFile={d.has_file} mimeType={d.mime_type} />
        </div>

        <div className="space-y-5">
          {result.isLoading && <Spinner label="Loading extraction" />}
          {result.isError && <ErrorNotice title="Couldn't load the extraction result" message={errorMessage(result.error)} />}

          {r && r.status === "failed" && (
            <ErrorNotice
              title={`Processing failed: ${r.diagnostics.error_code ?? "unknown error"}`}
              message={`${r.diagnostics.error_message ?? ""} This is reported as a failure, not as a non-invoice.`}
              action={d.has_file ? <Link className="btn btn-quiet" to={`/review/${id}`}>Enter values manually</Link> : undefined}
            />
          )}

          {r && docLevel.length > 0 && (
            <section className="rounded-lg border border-audit/30 bg-audit-soft px-5 py-4">
              <h2 className="flex items-center gap-2 font-semibold text-audit"><Flag className="size-4" aria-hidden /> Flagged for review</h2>
              <ul className="mt-2 space-y-1 text-sm text-ink-soft">
                {docLevel.map((i) => (
                  <li key={i.code + i.message}><strong className="font-semibold">{humanize(i.code)}:</strong> {i.message}</li>
                ))}
              </ul>
            </section>
          )}

          {r && invoice && invoice.is_invoice === false && <NonInvoiceNotice docType={r.diagnostics.document_type} />}

          {r && invoice && invoice.is_invoice && (
            <>
              <Section
                title="Extracted fields"
                aside={r.review_reasons.length > 0 && !r.corrected ? <span className="text-[13px] text-audit">{r.review_reasons.length} flag{r.review_reasons.length > 1 ? "s" : ""}</span> : undefined}
              >
                <InvoiceFields invoice={invoice} issues={r.corrected ? [] : issues} />
              </Section>
              <Section title={`Line items (${invoice.line_items?.length ?? 0})`}>
                <LineItemsTable invoice={invoice} issues={r.corrected ? [] : issues} />
              </Section>
              <Section title="Does it add up?">
                <ReconciliationTape
                  items={invoice.line_items}
                  tax={invoice.tax_total}
                  total={invoice.total}
                  currency={invoice.currency}
                  check={r.corrected ? r.corrected_validation : r.diagnostics.validation?.financial}
                  extras={r.diagnostics.extras}
                />
              </Section>
            </>
          )}

          {r && r.status === "success" && (
            <Section title="Validation">
              <ValidationList result={r} />
            </Section>
          )}

          {r && r.diagnostics.status && (
            <Section title="How it was processed">
              {r.status === "failed" && !r.diagnostics.attempts?.length ? (
                <p className="flex items-center gap-2 text-sm text-muted"><XCircle className="size-4" aria-hidden /> Stopped before any model call.</p>
              ) : (
                <ProcessingDetails result={r} />
              )}
            </Section>
          )}
        </div>
      </div>
    </>
  );
}
