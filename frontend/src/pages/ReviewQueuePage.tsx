import { Link } from "react-router-dom";
import { CheckCircle2, Flag } from "lucide-react";
import { useConfig, useDocuments } from "../hooks/useApi";
import { errorMessage } from "../services/api";
import { DocumentStatus, EmptyState, ErrorNotice, PageHeader, Spinner } from "../components/ui";
import { money, relativeTime } from "../lib/format";

export function ReviewQueuePage() {
  const docs = useDocuments({ review: "pending", limit: 200 });
  const config = useConfig();
  const labels = config.data?.review_reason_labels ?? {};

  return (
    <>
      <PageHeader
        title="Review queue"
        description="Documents the pipeline wasn't sure about. Check each flag against the original, correct what's wrong, then approve."
      />
      <section className="panel overflow-hidden">
        {docs.isLoading ? (
          <div className="p-5"><Spinner label="Loading queue" /></div>
        ) : docs.isError ? (
          <div className="p-5"><ErrorNotice title="Couldn't load the queue" message={errorMessage(docs.error)} /></div>
        ) : docs.data!.items.length === 0 ? (
          <EmptyState icon={<CheckCircle2 className="size-9 text-ledger" />} title="Nothing waiting for review">
            New flagged documents will appear here.
          </EmptyState>
        ) : (
          <ul>
            {docs.data!.items.map((d) => (
              <li key={d.id} className="border-b border-rule-soft last:border-0">
                <Link to={`/review/${d.id}`} className="grid gap-3 px-5 py-4 hover:bg-paper md:grid-cols-[minmax(0,1.2fr)_minmax(0,2fr)_auto] md:items-center">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{d.original_filename}</p>
                    <p className="truncate text-sm text-muted">
                      {d.status === "failed" ? d.error_code : [d.vendor, d.total != null ? money(d.total, d.currency) : null].filter(Boolean).join(", ") || "No values extracted"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {d.review_reasons.map((code) => (
                      <span key={code} className="inline-flex items-center gap-1 rounded border border-audit/25 bg-audit-soft px-1.5 py-0.5 text-[12.5px] text-audit">
                        <Flag className="size-3" aria-hidden /> {labels[code] ?? code}
                      </span>
                    ))}
                  </div>
                  <div className="flex items-center gap-3 md:justify-end">
                    <DocumentStatus doc={d} />
                    <span className="whitespace-nowrap text-sm text-muted">{relativeTime(d.created_at)}</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
