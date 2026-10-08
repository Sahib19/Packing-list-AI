from __future__ import annotations

import io
import json
import threading
import time
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .schema import Box, Item, PackingList


MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.6-flash")
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()


class ExtractedItem(BaseModel):
    code: str = Field(description="Product code such as PC-404 or FC-25; empty if unreadable")
    size: str = Field(description="Size such as S, M, L or XL; empty if not shown")
    quantity: int | None = Field(description="Number written beside this size; null if unclear")
    note: str = Field(description="ONLY BASE, ONLY CUP, or other handwritten note")
    source_text: str = Field(description="Short literal transcription of the line(s) used")
    needs_review: bool = Field(description="True if handwriting or interpretation is uncertain")


class ExtractedBox(BaseModel):
    number: int
    items: list[ExtractedItem]
    needs_review: bool


class ExtractedPage(BaseModel):
    customer: str = ""
    private_mark: str = ""
    boxes: list[ExtractedBox]
    warnings: list[str] = Field(default_factory=list)


PROMPT = """Read this photographed handwritten packing-list page carefully. Return every box and every ACTIVE item as structured data.

Rules:
1. A heading such as Box-13 starts a new box. Keep the exact box number. Never invent a missing box.
2. Ignore lines fully crossed out. If a line is corrected, take the final uncrossed text and flag needs_review if uncertain.
3. Carry the product code forward on a following row that only contains a size. Example: PC-404 M 3, then S 1 means PC-404 M x3 and PC-404 S x1.
4. If one handwritten item shows multiple sizes with one shared quantity, create ONE item per size, each with that quantity. Example: PC-405 S M L 5 becomes PC-405 S x5, PC-405 M x5, PC-405 L x5.
5. Distinguish similar product prefixes and digits carefully: PC, FC, PF, AA and W can all occur. Keep any other code or product name shown. The verified vocabulary below is only a spelling hint.
6. Preserve ONLY BASE and ONLY CUP as notes. Do not turn a note into an item or a quantity.
7. Do not guess an unreadable quantity or code. Return null/empty and needs_review=true; describe uncertainty in warnings.
8. Handwriting can extend into the printed PACKED BY footer. Treat active handwritten product entries there as items when they belong to the final box.
9. Return boxes in image order. Do not include blank printed rows.
10. Source text should quote only a short visible fragment for review. Never fill a row merely to reach a minimum count; blank rows are added at export time.
11. Finished sheets can contain named products such as OLYMPIC MEDAL 2.5" (GOLDEN). Put that ENTIRE name in code and leave size empty. GOLDEN/SILVER/BRONZE is a colour inside the product name, not a separate G/S/B size.
"""


def prepare_image(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        if max(image.size) > 2800:
            image.thumbnail((2800, 2800), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=93, optimize=True)
        return out.getvalue()


def _parse_response(text: str) -> ExtractedPage:
    try:
        return ExtractedPage.model_validate_json(text)
    except Exception:
        return ExtractedPage.model_validate(json.loads(text))


def extract_page(image_bytes: bytes, page_no: int, api_key: str, guidance: str = "") -> ExtractedPage:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))
    errors: list[str] = []
    with _cooldown_lock:
        available = [model for model in (MODEL, *FALLBACK_MODELS) if _cooldown_until.get(model, 0) <= time.monotonic()]
    if not available:
        available = [MODEL, *FALLBACK_MODELS]
    for model in available:
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    PROMPT + guidance,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedPage,
                    temperature=0,
                ),
            )
            if not response.text:
                raise ValueError("Gemini returned no readable output")
            page = _parse_response(response.text)
            if model != MODEL:
                page.warnings.append(f"Recognition used backup model {model} because the primary model was unavailable")
            return page
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:350]}")
            message = str(exc).lower()
            transient = any(token in message for token in ("429", "rate", "quota", "503", "504", "temporar", "unavailable", "high demand", "404", "not_found", "timeout", "timed out"))
            if transient:
                with _cooldown_lock:
                    _cooldown_until[model] = time.monotonic() + 10 * 60
            else:
                break
    raise RuntimeError(f"Image {page_no}: recognition failed. " + " | ".join(errors))


def combine_pages(pages: list[ExtractedPage]) -> PackingList:
    result = PackingList()
    by_number: dict[int, Box] = {}
    for page_no, page in enumerate(pages, start=1):
        if not result.customer and page.customer.strip():
            result.customer = page.customer.strip()
        if not result.private_mark and page.private_mark.strip():
            result.private_mark = page.private_mark.strip()
        result.warnings.extend(f"Page {page_no}: {warning}" for warning in page.warnings)
        for extracted_box in page.boxes:
            if extracted_box.number < 1:
                result.warnings.append(f"Page {page_no}: invalid box number skipped")
                continue
            if extracted_box.number in by_number:
                box = by_number[extracted_box.number]
                box.needs_review = True
                result.warnings.append(f"Box {box.number} appears on multiple pages; review merged entries")
            else:
                box = Box(number=extracted_box.number, needs_review=extracted_box.needs_review)
                by_number[box.number] = box
            for item in extracted_box.items:
                values = item.model_dump()
                if "(" in values["code"] and ")" in values["code"] and values["size"].upper() in {"G", "S", "B"}:
                    colour = values["code"].rsplit("(", 1)[-1].rstrip(") ").upper()
                    if colour in {"GOLDEN", "SILVER", "BRONZE"} and values["size"].upper() == colour[0]:
                        values["size"] = ""
                box.items.append(Item(**values, source_page=page_no))
    result.boxes = sorted(by_number.values(), key=lambda box: box.number)
    if result.boxes:
        expected = set(range(result.boxes[0].number, result.boxes[-1].number + 1))
        missing = sorted(expected - set(by_number))
        if missing:
            result.warnings.append("Missing box numbers: " + ", ".join(map(str, missing)))
    return result

