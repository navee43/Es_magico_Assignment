import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CorrectedInvoice, type DocumentFilters } from "../services/api";

export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 5 * 60_000 });

export const useStats = () => useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 15_000 });

export const useDocuments = (filters: DocumentFilters) =>
  useQuery({ queryKey: ["documents", filters], queryFn: () => api.documents(filters), placeholderData: (prev) => prev });

export const useDocument = (id: string) => useQuery({ queryKey: ["document", id], queryFn: () => api.document(id) });

export const useResult = (id: string) =>
  useQuery({
    queryKey: ["result", id],
    queryFn: () => api.result(id),
    retry: (count, error: unknown) => count < 20 && (error as { response?: { status?: number } })?.response?.status === 409,
    retryDelay: 3000,
  });

export const useEvaluation = () => useQuery({ queryKey: ["evaluation"], queryFn: api.evaluation, retry: false });

export function useReview(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { action: "save" | "approve" | "reopen"; corrected?: CorrectedInvoice | null; note?: string | null }) =>
      api.review(id, body),
    onSuccess: (data) => {
      qc.setQueryData(["result", id], data);
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

/** Fetches the original file with the auth header and exposes it as an object URL. */
export function useDocumentFile(id: string, enabled: boolean) {
  const query = useQuery({ queryKey: ["file", id], queryFn: () => api.file(id), enabled, staleTime: Infinity, retry: 1 });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data) return;
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);
  return { ...query, url, mime: query.data?.type ?? null };
}
