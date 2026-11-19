import { AlertTriangle, BarChart3, CheckCircle2, Flag } from "lucide-react";
import { useEvaluation, useStats } from "../hooks/useApi";
import { errorCode, errorMessage } from "../services/api";
import { EmptyState, ErrorNotice, PageHeader, Spinner } from "../components/ui";
import { count, duration, percent, usd } from "../lib/format";
import type { EvaluationReport } from "../types/api";

const FIELD_LABELS: Record<string, string> = {
  is_invoice: "Invoice or not",
  vendor: "Vendor",
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  currency: "Currency",
  line_items: "Line items",
  tax_total: "Tax total",
  total: "Total",
};

function Bar({ value, tone = "bg-ledger" }: { value: number; tone?: string }) {
  return (
    <div className="h-2 flex-1 rounded-full bg-rule-soft" aria-hidden>
      <div className={`h-2 rounded-full ${tone}`} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

function LiveUsage() {
  const stats = useStats();
  if (!stats.data) return null;
  const s = stats.data;
  return (
    <section className="panel p-5">
      <h2 className="text-[15px] font-semibold">Live usage on this deployment</h2>
      <p className="text-sm text-muted">From every document processed here, not just the labelled set.</p>
      <dl className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
        <div><dt className="text-muted">Documents billed</dt><dd className="num text-lg font-semibold">{count(s.billed_documents)}</dd></div>
        <div><dt className="text-muted">Total tokens</dt><dd className="num text-lg font-semibold">{count(s.total_tokens)}</dd></div>
        <div><dt className="text-muted">Avg cost / doc</dt><dd className="num text-lg font-semibold">{usd(s.avg_cost_usd, 5)}</dd></div>
        <div><dt className="text-muted">Total spend</dt><dd className="num text-lg font-semibold">{usd(s.total_cost_usd, 4)}</dd></div>
        <div><dt className="text-muted">Median latency</dt><dd className="num text-lg font-semibold">{duration(s.p50_processing_ms)}</dd></div>
        <div><dt className="text-muted">90th percentile</dt><dd className="num text-lg font-semibold">{duration(s.p90_processing_ms)}</dd></div>
      </dl>
    </section>
  );
}

function Report({ report }: { report: EvaluationReport }) {
  const { meta, metrics: m, failures } = report;
  const cm = m.classification.confusion_matrix;
  const r = m.review;
  const categories = Object.entries(failures.category_counts);
  const maxCat = Math.max(1, ...categories.map(([, v]) => v.errors));
  const headline = [
    { label: "Exact-match documents", value: percent(m.full_document_exact_match_rate), note: "all fields right" },
    { label: "Field accuracy", value: percent(m.overall_field_accuracy_on_invoices), note: "on invoices only" },
    { label: "Classification F1", value: percent(m.classification.f1), note: "invoice vs not" },
    { label: "Line-item F1", value: percent(m.line_items.f1), note: `${m.line_items.matched_rows}/${m.line_items.expected_rows} rows matched` },
    { label: "Avg cost / document", value: usd(m.performance.avg_cost_usd, 5), note: `${duration(m.performance.avg_processing_ms)} avg time` },
  ];

  return (
    <div className="space-y-6">
      {(meta.synthetic || meta.llm_provider === "mock") && (
        <div className="flex items-center gap-2 rounded-lg border border-audit/30 bg-audit-soft px-4 py-3 text-sm text-audit">
          <AlertTriangle className="size-4 shrink-0" aria-hidden />
          {meta.synthetic ? "These numbers come from synthetic fixtures, not the assignment dataset." : "These numbers come from the mock model, not a real LLM."}
        </div>
      )}

      <section className="panel grid grid-cols-2 divide-rule sm:grid-cols-5 sm:divide-x" aria-label="Headline metrics">
        {headline.map((h) => (
          <div key={h.label} className="px-5 py-4">
            <p className="text-[13px] text-muted">{h.label}</p>
            <p className="num mt-1 text-[28px] font-semibold leading-none">{h.value}</p>
            <p className="mt-1 text-xs text-muted">{h.note}</p>
          </div>
        ))}
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="panel p-5">
          <h2 className="text-[15px] font-semibold">Accuracy by field</h2>
          <p className="text-sm text-muted">Invoice fields are scored on the {m.counts.ground_truth_invoices} ground-truth invoices only, so non-invoices can't inflate them.</p>
          <ul className="mt-4 space-y-3">
            {m.field_accuracy.map((row) => (
              <li key={row.field} className="grid grid-cols-[8.5rem_1fr_4.5rem] items-center gap-3 text-sm" title={`${row.correct} of ${row.n} correct; ${row.wrong} wrong, ${row.missing} missing, ${row.spurious} spurious, ${row.failed} failed`}>
                <span className="text-ink-soft">{FIELD_LABELS[row.field] ?? row.field}</span>
                <Bar value={row.accuracy ?? 0} />
                <span className="num text-right"><strong className="font-semibold">{percent(row.accuracy)}</strong> <span className="text-muted">{row.correct}/{row.n}</span></span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-muted">Non-invoices with every invoice field left null: {percent(m.non_invoice_null_compliance)}.</p>
        </section>

        <section className="panel p-5">
          <h2 className="text-[15px] font-semibold">Human-review flag</h2>
          <p className="text-sm text-muted">A document counts as wrong if any field differs from the ground truth or processing failed.</p>
          <div className="mt-4 grid grid-cols-[auto_1fr_1fr] gap-px overflow-hidden rounded-lg border border-rule bg-rule text-sm">
            <div className="bg-paper" />
            <div className="bg-paper px-3 py-2 font-medium">Actually wrong</div>
            <div className="bg-paper px-3 py-2 font-medium">Actually right</div>
            <div className="bg-paper px-3 py-3 font-medium">Flagged</div>
            <div className="bg-white px-3 py-3"><span className="num text-xl font-semibold text-ledger">{r.incorrect_caught}</span><span className="block text-xs text-muted">errors caught</span></div>
            <div className="bg-white px-3 py-3"><span className="num text-xl font-semibold">{r.correct_but_flagged}</span><span className="block text-xs text-muted">unneeded reviews</span></div>
            <div className="bg-paper px-3 py-3 font-medium">Not flagged</div>
            <div className="bg-white px-3 py-3"><span className="num text-xl font-semibold text-fail">{r.incorrect_missed}</span><span className="block text-xs text-muted">errors missed</span></div>
            <div className="bg-white px-3 py-3"><span className="num text-xl font-semibold">{r.correct_not_flagged}</span><span className="block text-xs text-muted">correctly passed</span></div>
          </div>
          <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
            <div><dt className="text-muted">Precision</dt><dd className="num text-lg font-semibold">{percent(r.precision)}</dd><dd className="text-xs text-muted">of flagged docs had errors</dd></div>
            <div><dt className="text-muted">Recall</dt><dd className="num text-lg font-semibold">{percent(r.recall)}</dd><dd className="text-xs text-muted">of wrong docs were flagged</dd></div>
            <div><dt className="text-muted">Flagged</dt><dd className="num text-lg font-semibold">{r.flagged}/{r.documents}</dd><dd className="text-xs text-muted">documents</dd></div>
          </dl>
          {r.missed_documents.length > 0 && <p className="mt-3 text-xs text-muted">Missed: {r.missed_documents.join(", ")}</p>}
        </section>

        <section className="panel p-5">
          <h2 className="text-[15px] font-semibold">Invoice classification</h2>
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="text-left text-[13px] text-muted">
                <th className="py-1.5 font-medium">Ground truth</th>
                <th className="py-1.5 text-right font-medium">Predicted invoice</th>
                <th className="py-1.5 text-right font-medium">Predicted non-invoice</th>
                <th className="py-1.5 text-right font-medium">Failed</th>
              </tr>
            </thead>
            <tbody className="num">
              {(["invoice", "non_invoice"] as const).map((row) => (
                <tr key={row} className="border-t border-rule-soft">
                  <td className="py-2 font-medium">{row === "invoice" ? "Invoice" : "Non-invoice"}</td>
                  {(["invoice", "non_invoice", "failed"] as const).map((col) => {
                    const correct = (row === col);
                    const n = cm[row][col];
                    return (
                      <td key={col} className={`py-2 text-right ${n && !correct ? "font-semibold text-fail" : correct ? "font-semibold" : "text-muted"}`}>{n}</td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
            <div><dt className="text-muted">Precision</dt><dd className="num text-lg font-semibold">{percent(m.classification.precision)}</dd></div>
            <div><dt className="text-muted">Recall</dt><dd className="num text-lg font-semibold">{percent(m.classification.recall)}</dd></div>
            <div><dt className="text-muted">F1</dt><dd className="num text-lg font-semibold">{percent(m.classification.f1)}</dd></div>
          </dl>
          <p className="mt-3 text-xs text-muted">
            Schema valid: {percent(m.validation.schema_validation_success_rate)}. Arithmetic check passed: {percent(m.validation.financial_validation_pass_rate)} of checkable invoices.
          </p>
        </section>

        <section className="panel p-5">
          <h2 className="text-[15px] font-semibold">Where errors come from</h2>
          {categories.length === 0 ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-ledger"><CheckCircle2 className="size-4" aria-hidden /> No field errors on the labelled set.</p>
          ) : (
            <>
              <ul className="mt-4 space-y-3">
                {categories.map(([cat, v]) => (
                  <li key={cat} className="grid grid-cols-[12rem_1fr_4.5rem] items-center gap-3 text-sm">
                    <span className="text-ink-soft">{cat}</span>
                    <Bar value={v.errors / maxCat} tone="bg-audit" />
                    <span className="num text-right">{v.errors} <span className="text-muted">in {v.documents}</span></span>
                  </li>
                ))}
              </ul>
              {failures.highest_priority && (
                <div className="mt-4 rounded-md bg-audit-soft p-3 text-sm">
                  <p className="flex items-center gap-1.5 font-semibold text-audit"><Flag className="size-4" aria-hidden /> Fix first: {failures.highest_priority.category}</p>
                  <p className="mt-1 text-ink-soft">{failures.highest_priority.recommendation}</p>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      <section className="panel overflow-hidden">
        <h2 className="px-5 pt-4 text-[15px] font-semibold">Every labelled document</h2>
        <div className="overflow-x-auto">
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="border-b border-rule text-left text-[13px] text-muted">
                <th className="px-5 py-2 font-medium">Document</th>
                <th className="px-3 py-2 font-medium">Ground truth</th>
                <th className="px-3 py-2 text-right font-medium">Fields right</th>
                <th className="px-3 py-2 font-medium">Wrong fields</th>
                <th className="px-3 py-2 font-medium">Flagged</th>
                <th className="px-5 py-2 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {m.per_document.map((row) => (
                <tr key={row.document} className="border-b border-rule-soft last:border-0">
                  <td className="px-5 py-2 font-medium">{row.document}</td>
                  <td className="px-3 py-2 text-ink-soft">{row.expected_is_invoice ? "Invoice" : "Non-invoice"}</td>
                  <td className="num px-3 py-2 text-right">{row.status === "failed" ? <span className="text-fail">failed</span> : `${row.correct_fields}/${row.total_fields}`}</td>
                  <td className="px-3 py-2 text-ink-soft">{row.wrong_fields.length ? row.wrong_fields.map((f) => FIELD_LABELS[f] ?? f).join(", ") : <span className="text-ledger">none</span>}</td>
                  <td className="px-3 py-2">
                    {row.needs_review ? (
                      <span className={row.exact_match ? "text-ink-soft" : "text-ledger"}>yes{row.exact_match ? " (was right)" : " (caught)"}</span>
                    ) : row.exact_match ? (
                      <span className="text-muted">no</span>
                    ) : (
                      <span className="font-semibold text-fail">no (missed)</span>
                    )}
                  </td>
                  <td className="num px-5 py-2 text-right">{usd(row.cost_usd, 5)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export function AnalyticsPage() {
  const evaluation = useEvaluation();
  const noReport = evaluation.isError && errorCode(evaluation.error) === "NO_EVALUATION";
  const meta = evaluation.data?.meta;

  return (
    <>
      <PageHeader
        title="Evaluation"
        description={
          meta
            ? `Last run ${new Date(meta.generated_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })} on ${meta.documents} labelled documents with ${meta.model}, routing ${meta.routing_mode}, prompt ${meta.prompt_version}.`
            : "Accuracy against the labelled ground truth, produced by the evaluation harness."
        }
      />
      <div className="space-y-6">
        {evaluation.isLoading ? (
          <Spinner label="Loading evaluation" />
        ) : noReport ? (
          <section className="panel">
            <EmptyState icon={<BarChart3 className="size-9" />} title="No evaluation has been run yet">
              Run <code className="rounded bg-rule-soft px-1">make eval</code> with the labelled documents in <code className="rounded bg-rule-soft px-1">data/labelled</code> and
              their ground truth in <code className="rounded bg-rule-soft px-1">data/ground_truth</code>. The report appears here automatically. Nothing on this page is hard-coded.
            </EmptyState>
          </section>
        ) : evaluation.isError ? (
          <ErrorNotice title="Couldn't load the evaluation report" message={errorMessage(evaluation.error)} />
        ) : (
          <Report report={evaluation.data!} />
        )}
        <LiveUsage />
      </div>
    </>
  );
}
