from __future__ import annotations

from datetime import date
from pydantic import BaseModel, Field, field_validator, model_validator


class Item(BaseModel):
    code: str = ""
    size: str = ""
    quantity: int | None = None
    note: str = ""
    source_page: int | None = None
    source_text: str = ""
    needs_review: bool = False

    @field_validator("code", "size", "note", "source_text", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("quantity")
    @classmethod
    def check_quantity(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value > 100000):
            raise ValueError("Quantity must be between 0 and 100000")
        return value


class Box(BaseModel):
    number: int = Field(ge=1, le=9999)
    items: list[Item] = Field(default_factory=list)
    needs_review: bool = False


class PackingList(BaseModel):
    customer: str = ""
    packing_date: str = ""
    private_mark: str = ""
    transport: str = ""
    boxes: list[Box] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("customer", "packing_date", "private_mark", "transport", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def unique_boxes(self) -> "PackingList":
        numbers = [box.number for box in self.boxes]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Box numbers must be unique")
        return self


def normalized_item_label(item: Item) -> str:
    code = item.code.upper().strip()
    size = item.size.upper().strip()
    note = item.note.upper().strip()
    label = " ".join(x for x in (code, size) if x)
    if note:
        label += f" ({note})"
    return label.strip()


def validate_for_export(data: PackingList) -> None:
    if not data.boxes:
        raise ValueError("Add at least one box before export")
    for box in data.boxes:
        for index, item in enumerate(box.items, start=1):
            if not item.code:
                raise ValueError(f"Box {box.number}, row {index}: item code is empty")
            if item.quantity is None or item.quantity < 1:
                raise ValueError(f"Box {box.number}, row {index}: quantity must be at least 1")

