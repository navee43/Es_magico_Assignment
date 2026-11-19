import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { FileUp, Loader2, ScanLine, X } from "lucide-react";
import { useConfig } from "../hooks/useApi";
import { api, errorCode, errorMessage } from "../services/api";
import { LocalPreview } from "../components/DocumentViewer";
import { ErrorNotice, PageHeader } from "../components/ui";
import { bytes } from "../lib/format";
import axios from "axios";

const ACCEPTED = ["application/pdf", "image/jpeg", "image/png"];
const STEPS = ["Reading the file", "Extracting text or running OCR", "Asking the model", "Checking the numbers"];

type Phase = { kind: "idle" } | { kind: "uploading"; progress: number } | { kind: "processing"; started: number } | { kind: "error"; message: string; documentId?: string };

export function UploadPage() {
  const config = useConfig();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [elapsed, setElapsed] = useState(0);
  const maxMb = config.data?.max_upload_mb ?? 15;
  const busy = phase.kind === "uploading" || phase.kind === "processing";

  useEffect(() => {
    if (phase.kind !== "processing") return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - phase.started) / 1000)), 500);
    return () => clearInterval(timer);
  }, [phase]);

  function choose(candidate: File | undefined) {
    if (!candidate) return;
    const okType = ACCEPTED.includes(candidate.type) || /\.(pdf|jpe?g|png)$/i.test(candidate.name);
    if (!okType) {
      setPhase({ kind: "error", message: `"${candidate.name}" isn't a PDF, JPG or PNG.` });
      return;
    }
    if (candidate.size > maxMb * 1024 * 1024) {
      setPhase({ kind: "error", message: `"${candidate.name}" is ${bytes(candidate.size)}; the limit is ${maxMb} MB.` });
      return;
    }
    setFile(candidate);
    setPhase({ kind: "idle" });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!busy) choose(e.dataTransfer.files[0]);
  }

  async function extract() {
    if (!file) return;
    setElapsed(0);
    setPhase({ kind: "uploading", progress: 0 });
    try {
      const response = await api.extract(file, (progress) => {
        setPhase(progress >= 1 ? { kind: "processing", started: Date.now() } : { kind: "uploading", progress });
      });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      navigate(`/documents/${response.document_id}`);
    } catch (err) {
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      let documentId: string | undefined;
      if (axios.isAxiosError(err)) {
        const body = err.response?.data as { document_id?: string; detail?: { document_id?: string } } | undefined;
        documentId = body?.document_id ?? body?.detail?.document_id;
      }
      const code = errorCode(err);
      setPhase({ kind: "error", message: (code ? `${code}: ` : "") + errorMessage(err), documentId });
    }
  }

  const stepIndex = phase.kind === "processing" ? Math.min(STEPS.length - 1, 1 + Math.floor(elapsed / 4)) : 0;

  return (
    <>
      <PageHeader title="Upload an invoice" description={`PDF, JPG or PNG up to ${maxMb} MB. Scans and phone photos work too.`} />

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`panel flex min-h-[420px] flex-col items-center justify-center border-2 border-dashed p-6 transition-colors ${
            dragging ? "border-ledger bg-ledger-soft" : "border-rule"
          }`}
        >
          {file ? (
            <div className="w-full">
              <div className="max-h-[440px] overflow-auto rounded bg-paper p-4">
                <LocalPreview file={file} />
              </div>
            </div>
          ) : (
            <div className="text-center">
              <FileUp className="mx-auto size-10 text-muted" aria-hidden />
              <p className="mt-3 font-semibold">Drop a document here</p>
              <p className="text-sm text-muted">or</p>
              <button className="btn btn-quiet mt-2" onClick={() => input.current?.click()}>
                Choose a file
              </button>
            </div>
          )}
          <input
            ref={input}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            className="sr-only"
            onChange={(e) => {
              choose(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>

        <aside className="space-y-4">
          <section className="panel p-5">
            <h2 className="text-[15px] font-semibold">File</h2>
            {file ? (
              <div className="mt-2 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{file.name}</p>
                  <p className="text-sm text-muted">{bytes(file.size)}</p>
                </div>
                {!busy && (
                  <button className="rounded p-1 text-muted hover:bg-rule-soft hover:text-ink" onClick={() => setFile(null)} aria-label="Remove file">
                    <X className="size-4" />
                  </button>
                )}
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted">No file selected.</p>
            )}

            <button className="btn btn-primary mt-4 w-full justify-center" disabled={!file || busy} onClick={extract}>
              {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ScanLine className="size-4" aria-hidden />}
              {busy ? "Extracting…" : "Extract invoice data"}
            </button>
            {file && !busy && (
              <button className="btn btn-quiet mt-2 w-full justify-center" onClick={() => input.current?.click()}>
                Choose a different file
              </button>
            )}
          </section>

          {phase.kind === "uploading" && (
            <section className="panel p-5" aria-live="polite">
              <p className="text-sm font-medium">Uploading {Math.round(phase.progress * 100)}%</p>
              <div className="mt-2 h-2 rounded-full bg-rule-soft">
                <div className="h-2 rounded-full bg-ink transition-[width]" style={{ width: `${phase.progress * 100}%` }} />
              </div>
            </section>
          )}

          {phase.kind === "processing" && (
            <section className="panel p-5" aria-live="polite">
              <p className="flex justify-between text-sm font-medium">
                <span>Processing</span>
                <span className="num text-muted">{elapsed}s</span>
              </p>
              <ol className="mt-3 space-y-2 text-sm">
                {STEPS.map((step, i) => (
                  <li key={step} className={`flex items-center gap-2 ${i < stepIndex ? "text-ledger" : i === stepIndex ? "font-medium" : "text-muted"}`}>
                    {i === stepIndex ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <span className={`size-2 rounded-full ${i < stepIndex ? "bg-ledger" : "bg-rule"} mx-1`} />}
                    {step}
                  </li>
                ))}
              </ol>
              <p className="mt-3 text-xs text-muted">Scans and photos usually take 5–30 seconds. The steps shown are estimates; the page opens the result as soon as it's ready.</p>
            </section>
          )}

          {phase.kind === "error" && (
            <ErrorNotice
              title="Extraction didn't complete"
              message={phase.message}
              action={phase.documentId ? <Link className="btn btn-quiet" to={`/documents/${phase.documentId}`}>Open the document record</Link> : undefined}
            />
          )}
        </aside>
      </div>
    </>
  );
}
