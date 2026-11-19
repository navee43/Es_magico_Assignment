import { Link } from "react-router-dom";
import { ArrowUpRight, ClipboardCheck, FileText, Upload } from "lucide-react";
import { useConfig, useStats } from "../hooks/useApi";
import { errorMessage } from "../services/api";
import { DocumentStatus, EmptyState, ErrorNotice, PageHeader, Spinner } from "../components/ui";
import { ROUTE_LABELS, count, duration, money, relativeTime, usd } from "../lib/format";

export function DashboardPage() {
  const stats = useStats();
  const config = useConfig();
  const labels = config.data?.review_reason_labels ?? {};

  if (stats.isLoading) return <Spinner label="Loading overview" />;
  if (stats.isError) return <ErrorNotice title="Couldn't load the overview" message={errorMessage(stats.error)} />;
  const s = stats.data!;

  const tally = [
    { label: "Processed", value: s.total_documents, to: "/documents" },
    { label: "Invoices extracted", value: s.invoices, to: "/documents?kind=invoice" },
    { label: "Not invoices", value: s.non_invoices, to: "/documents?kind=non_invoice" },
    { label: "Needs review", value: s.needs_review, to: "/review", tone: s.needs_review ? "text-audit" : "" },
    { label: "Failed", value: s.failed, to: "/documents?status=failed", tone: s.failed ? "text-fail" : "" },
  ];
  const reasons = Object.entries(s.review_reasons);
  const maxReason = Math.max(1, ...reasons.map(([, n]) => n));
  const routes = Object.entries(s.routes);
  const routeTotal = routes.reduce((a, [, n]) => a + n, 0) || 1;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Everything extracted so far, what still needs a person, and what it cost."
        actions={
          <Link to="/upload" className="btn btn-primary">
            <Upload className="size-4" aria-hidden /> Upload invoice
          </Link>
        }
      />

      {s.total_documents === 0 ? (
        <div className="panel">
          <EmptyState icon={<FileText className="size-9" />} title="No documents yet">
            Upload a PDF, JPG or PNG invoice to see extracted fields, validation checks and cost here.
            <div className="mt-4">
              <Link to="/upload" className="btn btn-primary">Upload your first invoice</Link>
            </div>
          </EmptyState>
        </div>
      ) : (
        <div className="space-y-6">
          {/* The tally reads left-to-right like a ledger line: total, then where each document ended up. */}
          <section className="panel grid grid-cols-2 divide-rule sm:grid-cols-5 sm:divide-x" aria-label="Document counts">
            {tally.map((t) => (
              <Link key={t.label} to={t.to} className="group px-5 py-4 hover:bg-paper">
                <p className="text-[13px] text-muted">{t.label}</p>
                <p className={`num mt-1 text-[30px] font-semibold leading-none ${t.tone ?? ""}`}>{count(t.value)}</p>
              </Link>
            ))}
          </section>

          {s.needs_review > 0 && (
            <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-audit/30 bg-audit-soft px-5 py-3.5">
              <p className="flex items-center gap-2 text-audit">
                <ClipboardCheck className="size-5" aria-hidden />
                <span>
                  <strong className="num">{s.needs_review}</strong> document{s.needs_review > 1 ? "s are" : " is"} waiting for a person to check flagged fields.
                </span>
              </p>
              <Link to="/review" className="btn btn-quiet">Open review queue</Link>
            </section>
          )}

          <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
            <section className="panel overflow-hidden">
              <div className="flex items-center justify-between px-5 pt-4">
                <h2 className="text-[15px] font-semibold">Recent documents</h2>
                <Link to="/documents" className="text-sm font-medium text-ledger hover:underline">See all</Link>
              </div>
              <div className="overflow-x-auto">
                <table className="mt-2 w-full text-sm">
                  <thead>
                    <tr className="border-b border-rule text-left text-[13px] text-muted">
                      <th className="px-5 py-2 font-medium">Document</th>
                      <th className="px-3 py-2 font-medium">Vendor</th>
                      <th className="px-3 py-2 text-right font-medium">Total</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-5 py-2 text-right font-medium">Added</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.recent_documents.map((d) => (
                      <tr key={d.id} className="border-b border-rule-soft last:border-0 hover:bg-paper">
                        <td className="max-w-[240px] px-5 py-2.5">
                          <Link to={`/documents/${d.id}`} className="block truncate font-medium hover:underline">{d.original_filename}</Link>
                        </td>
                        <td className="max-w-[200px] truncate px-3 py-2.5 text-ink-soft">{d.vendor ?? "—"}</td>
                        <td className="num px-3 py-2.5 text-right">{d.total != null ? money(d.total, d.currency) : "—"}</td>
                        <td className="px-3 py-2.5"><DocumentStatus doc={d} /></td>
                        <td className="whitespace-nowrap px-5 py-2.5 text-right text-muted">{relativeTime(d.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <div className="space-y-6">
              <section className="panel p-5">
                <h2 className="text-[15px] font-semibold">Cost and speed</h2>
                <dl className="mt-3 grid grid-cols-2 gap-y-3 text-sm">
                  <div><dt className="text-muted">Avg model cost</dt><dd className="num text-lg font-semibold">{usd(s.avg_cost_usd, 5)}</dd></div>
                  <div><dt className="text-muted">Avg processing</dt><dd className="num text-lg font-semibold">{duration(s.avg_processing_ms)}</dd></div>
                  <div><dt className="text-muted">Total model spend</dt><dd className="num font-medium">{usd(s.total_cost_usd, 4)}</dd></div>
                  <div><dt className="text-muted">Tokens used</dt><dd className="num font-medium">{count(s.total_tokens)}</dd></div>
                </dl>
                <p className="mt-3 text-xs text-muted">
                  Averages cover {s.billed_documents} documents that needed model calls; {s.cached_results} cached repeats cost nothing.
                </p>
              </section>

              <section className="panel p-5">
                <h2 className="text-[15px] font-semibold">Why documents were flagged</h2>
                {reasons.length === 0 ? (
                  <p className="mt-2 text-sm text-muted">Nothing has been flagged.</p>
                ) : (
                  <ul className="mt-3 space-y-2.5">
                    {reasons.slice(0, 7).map(([code, n]) => (
                      <li key={code} className="text-sm" title={`${labels[code] ?? code}: ${n}`}>
                        <div className="flex justify-between gap-2">
                          <span className="text-ink-soft">{labels[code] ?? code}</span>
                          <span className="num font-medium">{n}</span>
                        </div>
                        <div className="mt-1 h-1.5 rounded-full bg-rule-soft">
                          <div className="h-1.5 rounded-full bg-audit" style={{ width: `${(n / maxReason) * 100}%` }} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {routes.length > 0 && (
                <section className="panel p-5">
                  <h2 className="text-[15px] font-semibold">What the model was given</h2>
                  <ul className="mt-3 space-y-1.5 text-sm">
                    {routes.map(([route, n]) => (
                      <li key={route} className="flex justify-between">
                        <span className="text-ink-soft">{ROUTE_LABELS[route] ?? route}</span>
                        <span className="num">{n} <span className="text-muted">({Math.round((n / routeTotal) * 100)}%)</span></span>
                      </li>
                    ))}
                  </ul>
                  <Link to="/analytics" className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-ledger hover:underline">
                    Accuracy on labelled data <ArrowUpRight className="size-3.5" aria-hidden />
                  </Link>
                </section>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
