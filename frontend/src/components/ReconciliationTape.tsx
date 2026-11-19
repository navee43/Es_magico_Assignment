import { CheckCircle2, CircleHelp, Flag } from "lucide-react";
import type { FinancialCheck, LineItem } from "../types/api";
import { money } from "../lib/format";

type Status = FinancialCheck["status"];

const toCents = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(v) ? null : Math.round(v * 100));

/** Mirrors the backend rule: sum(line amounts) + tax ≈ total, with adjustments as explanations. */
export function reconcile(
  items: LineItem[] | null,
  tax: number | null,
  total: number | null,
  extras?: { discount_total?: number | null; other_charges_total?: number | null },
): { status: Status; sum: number | null; expected: number | null; difference: number | null; explanation?: string } {
  const amounts = (items ?? []).map((i) => toCents(i.amount));
  const totalC = toCents(total);
  if (totalC === null || amounts.length === 0 || amounts.some((a) => a === null)) {
    return { status: "insufficient_data", sum: null, expected: null, difference: null };
  }
  const sum = (amounts as number[]).reduce((a, b) => a + b, 0);
  const taxC = toCents(tax) ?? 0;
  const expected = sum + taxC;
  const tol = 5 + amounts.length; // 0.05 + 0.01 per line, in cents
  const diff = totalC - expected;
  const base = { sum: sum / 100, expected: expected / 100, difference: diff / 100 };
  if (Math.abs(diff) <= tol) return { status: "pass", ...base };
  const adj = -(Math.abs(toCents(extras?.discount_total) ?? 0)) + (toCents(extras?.other_charges_total) ?? 0);
  if (adj !== 0 && Math.abs(totalC - (expected + adj)) <= tol) {
    return { status: "pass_with_adjustments", ...base, explanation: "Explained by a printed discount or extra charge." };
  }
  if (taxC && Math.abs(totalC - sum) <= tol) {
    return { status: "pass_with_adjustments", ...base, explanation: "Line amounts appear to already include tax." };
  }
  return { status: "mismatch", ...base };
}

export function ReconciliationTape({
  items,
  tax,
  total,
  currency,
  check,
  extras,
}: {
  items: LineItem[] | null;
  tax: number | null;
  total: number | null;
  currency: string | null;
  check?: FinancialCheck | null;
  extras?: { discount_total?: number | null; other_charges_total?: number | null };
}) {
  const local = reconcile(items, tax, total, extras);
  const status = check?.status && check.status !== "not_applicable" ? check.status : local.status;
  const explanation = check?.explanation ?? local.explanation;
  const rows = items ?? [];
  const shown = rows.slice(0, 8);
  const hidden = rows.slice(8);
  const hiddenSum = hidden.reduce((s, i) => s + (i.amount ?? 0), 0);

  const verdict =
    status === "pass" ? (
      <span className="flex items-center gap-1.5 text-ledger"><CheckCircle2 className="size-4" aria-hidden /> Adds up</span>
    ) : status === "pass_with_adjustments" ? (
      <span className="flex items-center gap-1.5 text-ledger"><CheckCircle2 className="size-4" aria-hidden /> Adds up with adjustments</span>
    ) : status === "mismatch" ? (
      <span className="flex items-center gap-1.5 text-audit"><Flag className="size-4" aria-hidden /> Doesn't add up</span>
    ) : (
      <span className="flex items-center gap-1.5 text-muted"><CircleHelp className="size-4" aria-hidden /> Can't check: missing amounts</span>
    );

  return (
    <figure className="tape num px-4 pb-3 pt-2 text-[13.5px] leading-[28px]" aria-label="Arithmetic check">
      {shown.map((item, i) => (
        <div key={i} className="flex justify-between gap-4">
          <span className="truncate text-ink-soft">{item.description || <em className="text-muted">no description</em>}</span>
          <span>{money(item.amount)}</span>
        </div>
      ))}
      {hidden.length > 0 && (
        <div className="flex justify-between gap-4 text-ink-soft">
          <span>{hidden.length} more line{hidden.length > 1 ? "s" : ""}</span>
          <span>{money(hiddenSum)}</span>
        </div>
      )}
      {rows.length === 0 && <div className="text-muted">No line items</div>}
      <div className="mt-1 flex justify-between border-t border-ink/60 pt-0.5 font-semibold">
        <span>Line items</span>
        <span>{money(local.sum)}</span>
      </div>
      <div className="flex justify-between">
        <span>+ Tax</span>
        <span>{tax === null ? "none" : money(tax)}</span>
      </div>
      <div className="flex justify-between">
        <span>= Expected total</span>
        <span>{money(local.expected)}</span>
      </div>
      <div className="flex justify-between font-semibold">
        <span>Printed total</span>
        <span>{money(total, currency)}</span>
      </div>
      <figcaption className="mt-1 flex flex-wrap items-center justify-between gap-2 border-t border-dashed border-rule pt-1 font-semibold">
        {verdict}
        {local.difference !== null && status !== "pass" && (
          <span className={status === "mismatch" ? "text-audit" : "text-muted"}>
            difference {local.difference > 0 ? "+" : ""}
            {money(local.difference)}
          </span>
        )}
      </figcaption>
      {explanation && status !== "mismatch" && <p className="text-xs leading-5 text-muted">{explanation}</p>}
    </figure>
  );
}
