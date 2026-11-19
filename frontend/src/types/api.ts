export interface LineItem {
  description: string | null;
  quantity: number | null;
  amount: number | null;
}

/** Exactly the assignment schema. is_invoice is null only when processing failed. */
export interface InvoiceResult {
  is_invoice: boolean | null;
  vendor: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string | null;
  line_items: LineItem[] | null;
  tax_total: number | null;
  total: number | null;
  needs_review: boolean;
}

export interface ReviewIssue {
  code: string;
  message: string;
  severity: "review" | "info";
}

export interface LLMAttempt {
  number: number;
  purpose: "initial" | "repair" | "validation_retry";
  route: string;
  model: string;
  http_calls: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  outcome: string;
  error: string | null;
}

export interface FinancialCheck {
  status: "pass" | "pass_with_adjustments" | "mismatch" | "insufficient_data" | "not_applicable";
  line_items_sum?: number;
  tax_total?: number | null;
  expected_total?: number;
  adjusted_expected_total?: number;
  total?: number;
  difference?: number;
  tolerance?: number;
  explanation?: string | null;
  message?: string;
}

export interface Diagnostics {
  status: "success" | "failed";
  error_code: string | null;
  error_message: string | null;
  route: string;
  route_reason: string | null;
  document_type: string | null;
  review_reasons: string[];
  review_details: ReviewIssue[];
  validation: {
    schema_valid?: boolean;
    schema_valid_first_try?: boolean;
    financial?: FinancialCheck;
    grounding?: Record<string, boolean>;
    checks?: { code: string; severity: string; message: string }[];
    retry_selected?: boolean;
  };
  llm_reported_issues: string[];
  extras: {
    subtotal?: number | null;
    discount_total?: number | null;
    other_charges_total?: number | null;
    invoice_date_as_printed?: string | null;
  };
  attempts: LLMAttempt[];
  usage: {
    models: string[];
    llm_attempts: number;
    http_calls: number;
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cost_usd: number;
    retry_cost_usd: number;
  };
  source: {
    kind: string;
    pages_total: number;
    pages_processed: number;
    digital_pages: number;
    ocr_pages: number;
    ocr_mean_confidence: number | null;
    ocr_word_count: number;
    ocr_error?: string | null;
    truncated_pages: boolean;
  };
  processing_ms: number;
  cached: boolean;
  original_cost_usd: number | null;
  prompt_version: string | null;
}

export type ReviewStatus = "not_required" | "pending" | "in_review" | "approved";

export interface DocumentSummary {
  id: string;
  original_filename: string;
  mime_type: string | null;
  size_bytes: number | null;
  pages: number | null;
  status: "processing" | "success" | "failed";
  is_invoice: boolean | null;
  needs_review: boolean;
  review_status: ReviewStatus;
  review_reasons: string[];
  error_code: string | null;
  error_message: string | null;
  route: string | null;
  document_type: string | null;
  vendor: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string | null;
  total: number | null;
  financial_status: string | null;
  model: string | null;
  total_tokens: number;
  cost_usd: number;
  attempts: number;
  cached: boolean;
  processing_ms: number | null;
  created_at: string;
  reviewed_at: string | null;
  review_note: string | null;
  has_file: boolean;
}

export interface DocumentList {
  items: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentResult {
  document_id: string;
  status: string;
  model_output: InvoiceResult | null;
  corrected: InvoiceResult | null;
  corrected_validation?: FinancialCheck | null;
  effective: InvoiceResult | null;
  review_reasons: string[];
  review_details: ReviewIssue[];
  diagnostics: Diagnostics;
  review_status: ReviewStatus;
  review_note: string | null;
  reviewed_at?: string | null;
}

export interface ExtractResponse {
  document_id: string;
  filename: string;
  result: InvoiceResult;
  review_reasons: string[];
  diagnostics: Diagnostics;
}

export interface Stats {
  total_documents: number;
  processing: number;
  invoices: number;
  non_invoices: number;
  failed: number;
  needs_review: number;
  flagged_total: number;
  approved: number;
  cached_results: number;
  billed_documents: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  avg_processing_ms: number | null;
  max_processing_ms: number | null;
  p50_processing_ms: number | null;
  p90_processing_ms: number | null;
  routes: Record<string, number>;
  error_codes: Record<string, number>;
  review_reasons: Record<string, number>;
  recent_documents: DocumentSummary[];
  llm_provider: string;
  llm_model: string;
}

export interface AppConfig {
  auth_enabled: boolean;
  llm_provider: string;
  llm_model: string;
  llm_vision_model: string;
  routing_mode: string;
  max_upload_mb: number;
  max_pdf_pages: number;
  prompt_version: string;
  review_reason_labels: Record<string, string>;
}

export interface FieldAccuracyRow {
  field: string;
  evaluated_on: string;
  n: number;
  correct: number;
  accuracy: number | null;
  wrong: number;
  missing: number;
  spurious: number;
  failed: number;
}

export interface EvaluationReport {
  meta: {
    generated_at: string;
    tag: string;
    synthetic: boolean;
    documents: number;
    llm_provider: string;
    model: string;
    vision_model: string;
    routing_mode: string;
    prompt_version: string;
    prompt_fingerprint: string;
  };
  metrics: {
    counts: { labelled_documents: number; ground_truth_invoices: number; ground_truth_non_invoices: number; processing_failures: number };
    field_accuracy: FieldAccuracyRow[];
    overall_field_accuracy_on_invoices: number | null;
    full_document_exact_match_rate: number | null;
    non_invoice_null_compliance: number | null;
    classification: {
      confusion_matrix: Record<"invoice" | "non_invoice", Record<"invoice" | "non_invoice" | "failed", number>>;
      precision: number | null;
      recall: number | null;
      f1: number | null;
      accuracy: number | null;
    };
    line_items: { expected_rows: number; predicted_rows: number; matched_rows: number; precision: number | null; recall: number | null; f1: number | null };
    validation: { schema_validation_success_rate: number | null; financial_validation_pass_rate: number | null; financial_status_counts: Record<string, number> };
    review: {
      documents: number;
      flagged: number;
      documents_with_errors: number;
      incorrect_caught: number;
      incorrect_missed: number;
      correct_but_flagged: number;
      correct_not_flagged: number;
      precision: number | null;
      recall: number | null;
      pct_flagged_with_errors: number | null;
      missed_documents: string[];
    };
    performance: { avg_processing_ms: number | null; median_processing_ms: number | null; avg_cost_usd: number | null; total_cost_usd: number; avg_tokens: number | null };
    per_document: {
      document: string;
      expected_is_invoice: boolean;
      status: string;
      route: string | null;
      correct_fields: number;
      total_fields: number;
      exact_match: boolean;
      wrong_fields: string[];
      needs_review: boolean;
      review_reasons: string[];
      cost_usd: number;
    }[];
  };
  failures: {
    category_counts: Record<string, { errors: number; documents: number }>;
    highest_priority: { category: string; errors: number; documents_affected: number; recommendation: string } | null;
    items: { document: string; field: string; category: string; expected: unknown; predicted: unknown; likely_reason: string }[];
  };
}

export interface ApiErrorBody {
  detail?: { code?: string; message?: string; errors?: string[]; document_id?: string } | string;
}
