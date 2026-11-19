import axios, { AxiosError } from "axios";
import type {
  ApiErrorBody,
  AppConfig,
  DocumentList,
  DocumentResult,
  DocumentSummary,
  EvaluationReport,
  ExtractResponse,
  InvoiceResult,
  Stats,
} from "../types/api";

const TOKEN_KEY = "invoice-ai-token";

export const tokenStore = {
  get: (): string | null => {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (token: string) => {
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: token lives for this page only */
    }
  },
  clear: () => {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

export const http = axios.create({ baseURL: "/api/v1", timeout: 180_000 });

http.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

http.interceptors.response.use(
  (r) => r,
  (error: AxiosError) => {
    if (error.response?.status === 401 && !error.config?.url?.includes("/auth/login")) {
      tokenStore.clear();
      window.dispatchEvent(new Event("invoice-ai:unauthorized"));
    }
    return Promise.reject(error);
  },
);

/** Turn any request failure into one human-readable sentence. */
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === "ECONNABORTED") return "The request took too long. The server may still be processing; check Documents in a minute.";
    if (!error.response) return "Can't reach the server. Check your connection and that the app is running.";
    const body = error.response.data as ApiErrorBody | undefined;
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.errors?.length ? `${detail.message} ${detail.errors.join("; ")}` : detail.message;
    return `The server returned HTTP ${error.response.status}.`;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

export function errorCode(error: unknown): string | undefined {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as ApiErrorBody | undefined)?.detail;
    if (detail && typeof detail !== "string") return detail.code;
  }
  return undefined;
}

export interface DocumentFilters {
  kind?: "invoice" | "non_invoice";
  status?: "success" | "failed" | "processing";
  review?: "pending" | "approved" | "not_required" | "in_review";
  q?: string;
  limit?: number;
  offset?: number;
}

export type CorrectedInvoice = Omit<InvoiceResult, "needs_review" | "is_invoice"> & { is_invoice: boolean };

export const api = {
  config: () => http.get<AppConfig>("/config").then((r) => r.data),
  login: (password: string) => http.post<{ token: string }>("/auth/login", { password }).then((r) => r.data),
  stats: () => http.get<Stats>("/stats").then((r) => r.data),
  documents: (filters: DocumentFilters) => http.get<DocumentList>("/documents", { params: filters }).then((r) => r.data),
  document: (id: string) => http.get<DocumentSummary>(`/documents/${id}`).then((r) => r.data),
  result: (id: string) => http.get<DocumentResult>(`/documents/${id}/result`).then((r) => r.data),
  file: (id: string) => http.get<Blob>(`/documents/${id}/file`, { responseType: "blob" }).then((r) => r.data),
  evaluation: () => http.get<EvaluationReport>("/evaluation").then((r) => r.data),
  review: (id: string, body: { action: "save" | "approve" | "reopen"; corrected?: CorrectedInvoice | null; note?: string | null }) =>
    http.patch<DocumentResult>(`/documents/${id}/review`, body).then((r) => r.data),
  extract: (file: File, onProgress: (fraction: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    return http
      .post<ExtractResponse>("/extract", form, {
        onUploadProgress: (e) => onProgress(e.total ? e.loaded / e.total : 0),
      })
      .then((r) => r.data);
  },
};
