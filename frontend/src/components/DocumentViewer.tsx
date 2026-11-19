import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { ChevronLeft, ChevronRight, FileWarning, Maximize2, Minus, Plus } from "lucide-react";
import { useDocumentFile } from "../hooks/useApi";
import { errorMessage } from "../services/api";
import { EmptyState, Spinner } from "./ui";
import { ErrorBoundary } from "./ErrorBoundary";

// Must be configured in the same module that renders <Document> (see react-pdf docs).
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

export function DocumentViewer({ id, hasFile, mimeType }: { id: string; hasFile: boolean; mimeType: string | null }) {
  const file = useDocumentFile(id, hasFile);
  const [pages, setPages] = useState(0);
  const [page, setPage] = useState(1);
  const [zoomIndex, setZoomIndex] = useState(2);
  const [width, setWidth] = useState(600);
  const frame = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width - 32)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const zoom = ZOOM_STEPS[zoomIndex];
  const isPdf = (file.mime ?? mimeType) === "application/pdf";

  return (
    <section className="panel flex h-full min-h-[420px] flex-col overflow-hidden" aria-label="Original document">
      <div className="flex items-center justify-between gap-2 border-b border-rule px-3 py-2 text-sm">
        <div className="flex items-center gap-1">
          {isPdf && (
            <>
              <button className="rounded p-1.5 hover:bg-rule-soft disabled:opacity-40" onClick={() => setPage((p) => p - 1)} disabled={page <= 1} aria-label="Previous page">
                <ChevronLeft className="size-4" />
              </button>
              <span className="num min-w-16 text-center text-ink-soft">
                {pages ? `${page} / ${pages}` : "–"}
              </span>
              <button className="rounded p-1.5 hover:bg-rule-soft disabled:opacity-40" onClick={() => setPage((p) => p + 1)} disabled={page >= pages} aria-label="Next page">
                <ChevronRight className="size-4" />
              </button>
            </>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button className="rounded p-1.5 hover:bg-rule-soft disabled:opacity-40" onClick={() => setZoomIndex((z) => z - 1)} disabled={zoomIndex === 0} aria-label="Zoom out">
            <Minus className="size-4" />
          </button>
          <span className="num w-12 text-center text-ink-soft">{Math.round(zoom * 100)}%</span>
          <button className="rounded p-1.5 hover:bg-rule-soft disabled:opacity-40" onClick={() => setZoomIndex((z) => z + 1)} disabled={zoomIndex === ZOOM_STEPS.length - 1} aria-label="Zoom in">
            <Plus className="size-4" />
          </button>
          <button className="rounded p-1.5 hover:bg-rule-soft" onClick={() => setZoomIndex(2)} aria-label="Fit to width" title="Fit to width">
            <Maximize2 className="size-4" />
          </button>
        </div>
      </div>
      <div ref={frame} className="flex-1 overflow-auto bg-[#e7ebe9] p-4">
        {!hasFile ? (
          <EmptyState icon={<FileWarning className="size-8" />} title="No preview">
            The original file wasn't stored because it was rejected at upload.
          </EmptyState>
        ) : file.isLoading ? (
          <Spinner label="Loading document" />
        ) : file.isError ? (
          <EmptyState icon={<FileWarning className="size-8" />} title="Preview unavailable">
            {errorMessage(file.error)}
          </EmptyState>
        ) : file.url && isPdf ? (
          <ErrorBoundary resetKey={file.url} fallback={<EmptyState icon={<FileWarning className="size-8" />} title="This PDF can't be displayed">It may be corrupted. The extraction result explains what happened.</EmptyState>}>
          <Document
            file={file.url}
            onLoadSuccess={({ numPages }) => {
              setPages(numPages);
              setPage(1);
            }}
            loading={<Spinner label="Rendering PDF" />}
            error={
              <EmptyState icon={<FileWarning className="size-8" />} title="This PDF can't be displayed">
                It may be corrupted. The extraction result on the right explains what happened.
              </EmptyState>
            }
          >
            <Page
              pageNumber={page}
              width={width * zoom}
              renderTextLayer={false}
              renderAnnotationLayer={false}
              className="mx-auto w-fit shadow-[0_1px_3px_rgba(27,42,56,0.18)]"
            />
          </Document>
          </ErrorBoundary>
        ) : file.url ? (
          <img
            src={file.url}
            alt="Uploaded document"
            style={{ width: width * zoom, maxWidth: "none" }}
            className="mx-auto block shadow-[0_1px_3px_rgba(27,42,56,0.18)]"
          />
        ) : null}
      </div>
    </section>
  );
}

/** Small first-page preview for files that haven't been uploaded yet. */
export function LocalPreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  if (!url) return null;
  const unreadable = <p className="p-4 text-sm text-muted">No preview: the browser couldn't read this PDF. You can still try extracting it; the server will report what's wrong.</p>;
  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
    return (
      <ErrorBoundary resetKey={url} fallback={unreadable}>
      <Document file={url} loading={<Spinner label="Reading PDF" />} error={unreadable} onLoadError={() => undefined}>
        <Page pageNumber={1} width={300} renderTextLayer={false} renderAnnotationLayer={false} className="mx-auto w-fit shadow-sm" />
      </Document>
      </ErrorBoundary>
    );
  }
  return <img src={url} alt="Selected file preview" className="mx-auto max-h-[420px] w-auto shadow-sm" />;
}
