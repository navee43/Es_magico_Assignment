import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FileSearch, Search } from "lucide-react";
import { useDocuments } from "../hooks/useApi";
import { errorMessage, type DocumentFilters } from "../services/api";
import { DocumentStatus, EmptyState, ErrorNotice, PageHeader, Spinner } from "../components/ui";
import { ROUTE_LABELS, longDate, money, relativeTime, usd } from "../lib/format";

const TABS: { key: string; label: string; filters: DocumentFilters }[] = [
  { key: "all", label: "All", filters: {} },
  { key: "invoice", label: "Invoices", filters: { kind: "invoice" } },
  { key: "non_invoice", label: "Not invoices", filters: { kind: "non_invoice" } },
  { key: "pending", label: "Needs review", filters: { review: "pending" } },
  { key: "approved", label: "Approved", filters: { review: "approved" } },
  { key: "failed", label: "Failed", filters: { status: "failed" } },
];
const PAGE = 25;

export function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  const tabKey = params.get("kind") ?? (params.get("status") === "failed" ? "failed" : params.get("tab") ?? "all");
  const tab = TABS.find((t) => t.key === tabKey) ?? TABS[0];
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);
  useEffect(() => setOffset(0), [tabKey, debounced]);

  const docs = useDocuments({ ...tab.filters, q: debounced || undefined, limit: PAGE, offset });

  return (
    <>
      <PageHeader title="Documents" description="Every upload, including rejected and failed files." />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1 rounded-lg border border-rule bg-white p-1" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={t.key === tab.key}
              onClick={() => setParams(t.key === "all" ? {} : { tab: t.key })}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${t.key === tab.key ? "bg-ink text-white" : "text-ink-soft hover:bg-rule-soft"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="relative w-full max-w-xs">
          <span className="sr-only">Search documents</span>
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
          <input className="input pl-8" placeholder="File name, vendor or invoice no." value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
      </div>

      <section className="panel overflow-hidden">
        {docs.isLoading ? (
          <div className="p-5"><Spinner label="Loading documents" /></div>
        ) : docs.isError ? (
          <div className="p-5"><ErrorNotice title="Couldn't load documents" message={errorMessage(docs.error)} /></div>
        ) : docs.data!.items.length === 0 ? (
          <EmptyState icon={<FileSearch className="size-9" />} title={debounced ? "No matches" : "Nothing here yet"}>
            {debounced ? "Try a different search." : <Link className="text-ledger hover:underline" to="/upload">Upload a document</Link>}
          </EmptyState>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-rule text-left text-[13px] text-muted">
                    <th className="px-5 py-2.5 font-medium">Document</th>
                    <th className="px-3 py-2.5 font-medium">Vendor</th>
                    <th className="px-3 py-2.5 font-medium">Invoice no.</th>
                    <th className="px-3 py-2.5 font-medium">Date</th>
                    <th className="px-3 py-2.5 text-right font-medium">Total</th>
                    <th className="px-3 py-2.5 font-medium">Status</th>
                    <th className="px-3 py-2.5 font-medium">Input</th>
                    <th className="px-3 py-2.5 text-right font-medium">Cost</th>
                    <th className="px-5 py-2.5 text-right font-medium">Added</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.data!.items.map((d) => (
                    <tr key={d.id} className="border-b border-rule-soft last:border-0 hover:bg-paper">
                      <td className="max-w-[260px] px-5 py-2.5">
                        <Link to={`/documents/${d.id}`} className="block truncate font-medium hover:underline">{d.original_filename}</Link>
                        {d.status === "failed" && <span className="text-xs text-fail">{d.error_code}</span>}
                      </td>
                      <td className="max-w-[200px] truncate px-3 py-2.5 text-ink-soft">{d.vendor ?? "—"}</td>
                      <td className="px-3 py-2.5 text-ink-soft">{d.invoice_number ?? "—"}</td>
                      <td className="num whitespace-nowrap px-3 py-2.5 text-ink-soft">{longDate(d.invoice_date)}</td>
                      <td className="num whitespace-nowrap px-3 py-2.5 text-right">{d.total != null ? money(d.total, d.currency) : "—"}</td>
                      <td className="px-3 py-2.5"><DocumentStatus doc={d} /></td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-ink-soft">{d.route ? ROUTE_LABELS[d.route] ?? d.route : "—"}</td>
                      <td className="num px-3 py-2.5 text-right text-ink-soft">{d.cached ? "cached" : usd(d.cost_usd, 5)}</td>
                      <td className="whitespace-nowrap px-5 py-2.5 text-right text-muted">{relativeTime(d.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-rule px-5 py-3 text-sm text-muted">
              <span className="num">
                {offset + 1}–{offset + docs.data!.items.length} of {docs.data!.total}
              </span>
              <div className="flex gap-2">
                <button className="btn btn-quiet" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>Previous</button>
                <button className="btn btn-quiet" disabled={offset + PAGE >= docs.data!.total} onClick={() => setOffset((o) => o + PAGE)}>Next</button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}
