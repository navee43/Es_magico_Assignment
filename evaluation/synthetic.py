"""Generate clearly-labelled SYNTHETIC documents + ground truth.

These exist only to exercise the pipeline and the evaluation harness without the
confidential assignment dataset. Scores computed on them are NOT assignment
results and are always written under a "synthetic" label.

    python -m evaluation.synthetic --out evaluation/fixtures/synthetic
"""

from __future__ import annotations

import argparse
import io
import json
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


INVOICES = {
    "synth_digital_inr": {
        "vendor": "Acme Supplies Pvt Ltd",
        "invoice_number": "INV-2041",
        "invoice_date": "2026-03-14",
        "printed_date": "14/03/2026",
        "currency": "INR",
        "symbol": "Rs.",
        "items": [("A4 paper, 500 sheets", 10, 320.00), ("Stapler heavy duty", 2, 450.00), ("Whiteboard markers (pack of 4)", 5, 120.00)],
        "tax_label": "GST 18%",
        "tax_rate": 0.18,
        "bill_to": "Globex Retail LLP",
    },
    "synth_digital_usd": {
        "vendor": "Northwind Traders Inc.",
        "invoice_number": "NW-88310",
        "invoice_date": "2026-01-07",
        "printed_date": "January 7, 2026",
        "currency": "USD",
        "symbol": "$",
        "items": [("Consulting services - January", 12, 150.00), ("Cloud hosting (monthly)", 1, 249.99)],
        "tax_label": "Sales tax 8.25%",
        "tax_rate": 0.0825,
        "bill_to": "Initech Corporation",
    },
    "synth_scanned_eur": {
        "vendor": "Bauer Werkzeuge GmbH",
        "invoice_number": "RE-2026-0042",
        "invoice_date": "2026-02-20",
        "printed_date": "20.02.2026",
        "currency": "EUR",
        "symbol": "EUR",
        "items": [("Drill bit set HSS", 3, 24.90), ("Safety gloves size L", 10, 6.50)],
        "tax_label": "VAT 19%",
        "tax_rate": 0.19,
        "bill_to": "Muster Bau AG",
    },
    "synth_photo_inr": {
        "vendor": "Sharma Electricals",
        "invoice_number": "SE/145/25-26",
        "invoice_date": "2026-03-02",
        "printed_date": "02-03-2026",
        "currency": "INR",
        "symbol": "Rs.",
        "items": [("LED bulb 9W", 12, 85.00), ("Extension board 4-way", 2, 399.00)],
        "tax_label": "GST 18%",
        "tax_rate": 0.18,
        "bill_to": "Kumar Residence",
    },
}


def _amounts(spec: dict) -> tuple[list[tuple[str, int, float, float]], float, float, float]:
    rows = [(d, q, p, round(q * p, 2)) for d, q, p in spec["items"]]
    subtotal = round(sum(r[3] for r in rows), 2)
    tax = round(subtotal * spec["tax_rate"], 2)
    return rows, subtotal, tax, round(subtotal + tax, 2)


def invoice_lines(spec: dict) -> list[tuple[str, bool]]:
    rows, subtotal, tax, total = _amounts(spec)
    sym = spec["symbol"]
    lines = [
        (spec["vendor"], True),
        ("12 Industrial Estate, Example City", False),
        ("", False),
        ("TAX INVOICE", True),
        (f"Invoice No: {spec['invoice_number']}", False),
        (f"Invoice Date: {spec['printed_date']}", False),
        (f"Bill To: {spec['bill_to']}", False),
        ("", False),
        ("Description                         Qty     Rate      Amount", True),
    ]
    for desc, qty, price, amount in rows:
        lines.append((f"{desc:<34} {qty:>4} {price:>9,.2f} {amount:>11,.2f}", False))
    lines += [
        ("", False),
        (f"Subtotal: {sym} {subtotal:,.2f}", False),
        (f"{spec['tax_label']}: {sym} {tax:,.2f}", False),
        (f"Grand Total: {sym} {total:,.2f}", True),
        ("", False),
        ("Thank you for your business.", False),
    ]
    return lines


def ground_truth(spec: dict) -> dict:
    rows, _subtotal, tax, total = _amounts(spec)
    return {
        "is_invoice": True,
        "vendor": spec["vendor"],
        "invoice_number": spec["invoice_number"],
        "invoice_date": spec["invoice_date"],
        "currency": spec["currency"],
        "line_items": [{"description": d, "quantity": q, "amount": a} for d, q, _p, a in rows],
        "tax_total": tax,
        "total": total,
        "needs_review": False,
    }


NON_INVOICE_GT = {
    "is_invoice": False, "vendor": None, "invoice_number": None, "invoice_date": None, "currency": None,
    "line_items": None, "tax_total": None, "total": None, "needs_review": False,
}


def digital_pdf(lines: list[tuple[str, bool]]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    for text, bold in lines:
        if text:
            page.insert_text((50, y), text, fontname="cobo" if bold else "cour", fontsize=10)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


def render_image(lines: list[tuple[str, bool]], width: int = 1240, height: int = 1754) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    regular, bold = _font(24), _font(28)
    mono = None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf"):
        if Path(path).is_file():
            mono = ImageFont.truetype(path, 22)
    y = 100
    for text, is_bold in lines:
        if text:
            font = bold if is_bold else (mono if (mono and "  " in text) else regular)
            draw.text((90, y), text, fill="black", font=font)
        y += 40
    return img


def scanned_pdf(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=70)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


def photo_jpeg(img: Image.Image, seed: int = 7) -> bytes:
    rnd = random.Random(seed)
    canvas = Image.new("RGB", (img.width + 200, img.height + 200), (95, 85, 70))
    canvas.paste(img, (100, 100))
    canvas = canvas.rotate(2.5, expand=False, fillcolor=(95, 85, 70), resample=Image.Resampling.BICUBIC)
    canvas = canvas.filter(ImageFilter.GaussianBlur(1.1))
    px = canvas.load()
    for _ in range(25000):
        x, y = rnd.randrange(canvas.width), rnd.randrange(canvas.height)
        v = rnd.randint(-40, 40)
        r, g, b = px[x, y]
        px[x, y] = (max(0, min(255, r + v)), max(0, min(255, g + v)), max(0, min(255, b + v)))
    canvas = canvas.resize((canvas.width * 3 // 4, canvas.height * 3 // 4))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def letter_pdf() -> bytes:
    lines = [
        ("Globex Retail LLP", True), ("", False), ("Date: 03 March 2026", False), ("", False),
        ("Subject: Change of office address", True), ("", False),
        ("Dear Partner,", False),
        ("Please note that from 1 April 2026 our registered office will move to", False),
        ("Plot 44, Sector 18, Example City. All correspondence should be sent to", False),
        ("the new address. No action is required on open orders.", False), ("", False),
        ("Regards,", False), ("Operations Team", False),
    ]
    return digital_pdf(lines)


def generate(out: Path) -> dict[str, Path]:
    docs = out / "documents"
    gt_dir = out / "ground_truth"
    docs.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    def save(name: str, data: bytes, gt: dict | None) -> None:
        path = docs / name
        path.write_bytes(data)
        written[name] = path
        if gt is not None:
            (gt_dir / f"{Path(name).stem}.json").write_text(json.dumps(gt, indent=2) + "\n")

    for key in ("synth_digital_inr", "synth_digital_usd"):
        save(f"{key}.pdf", digital_pdf(invoice_lines(INVOICES[key])), ground_truth(INVOICES[key]))
    spec = INVOICES["synth_scanned_eur"]
    save("synth_scanned_eur.pdf", scanned_pdf(render_image(invoice_lines(spec))), ground_truth(spec))
    spec = INVOICES["synth_photo_inr"]
    save("synth_photo_inr.jpg", photo_jpeg(render_image(invoice_lines(spec))), ground_truth(spec))
    rotated = render_image(invoice_lines(INVOICES["synth_digital_inr"])).rotate(90, expand=True)
    buf = io.BytesIO()
    rotated.save(buf, format="PNG")
    save("synth_rotated_inr.png", buf.getvalue(), ground_truth(INVOICES["synth_digital_inr"]))
    save("synth_letter.pdf", letter_pdf(), NON_INVOICE_GT)
    save("synth_corrupted.pdf", b"%PDF-1.7\n1 0 obj << /Type /Catalog >>\n%% truncated garbage \x00\x01", None)
    (out / "README.md").write_text(
        "# SYNTHETIC fixtures\n\nGenerated by `python -m evaluation.synthetic`. These are NOT the assignment\n"
        "dataset; any metric computed on them only demonstrates that the harness works.\n"
    )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="evaluation/fixtures/synthetic")
    args = parser.parse_args()
    written = generate(Path(args.out))
    print(f"Wrote {len(written)} synthetic documents to {args.out}")


if __name__ == "__main__":
    main()
