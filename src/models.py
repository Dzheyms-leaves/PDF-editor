"""Pydantic schemas shared by the API layer and the PDF/OCR services."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

class Rect(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return abs(self.x1 - self.x0)

    @property
    def height(self) -> float:
        return abs(self.y1 - self.y0)

    def normalised(self) -> "Rect":
        return Rect(
            x0=min(self.x0, self.x1),
            y0=min(self.y0, self.y1),
            x1=max(self.x0, self.x1),
            y1=max(self.y0, self.y1),
        )

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


class ObstacleBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    obstacle_type: str = "text"  # text | drawing | image | margin | pixel
    label: Optional[str] = None


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

class PageInfo(BaseModel):
    page_number: int  # 1-indexed
    width: float
    height: float
    rotation: int = 0
    has_text: bool = True
    label: Optional[str] = None


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    total_pages: int
    pages: List[PageInfo] = []
    is_encrypted: bool = False
    has_form_fields: bool = False
    text_coverage: float = 1.0  # 0.0 = fully scanned image, 1.0 = full text layer
    revision: int = 0
    can_undo: bool = False
    can_redo: bool = False


class WordBox(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    block: int = 0
    line: int = 0
    word: int = 0


class PageTextLayer(BaseModel):
    page_number: int
    width: float
    height: float
    words: List[WordBox] = []
    source: str = "pdf"  # pdf | ocr


# --------------------------------------------------------------------------
# Page operations
# --------------------------------------------------------------------------

class PageSelection(BaseModel):
    """Page targeting shared by editing and stamping operations."""

    mode: Literal["all", "first", "last", "all_except_first", "even", "odd", "custom"] = "all"
    custom: Optional[str] = Field(default=None, description="e.g. '1, 3-5, 9'")


class RotateRequest(BaseModel):
    pages: List[int]
    degrees: int = 90  # relative rotation, multiple of 90


class ReorderRequest(BaseModel):
    order: List[int] = Field(description="New 1-indexed page order; must be a permutation")


class DeletePagesRequest(BaseModel):
    pages: List[int]


class MovePageRequest(BaseModel):
    page: int
    to_index: int  # 1-indexed destination


class DuplicatePagesRequest(BaseModel):
    pages: List[int]


class InsertBlankRequest(BaseModel):
    after_page: int = 0  # 0 = insert at the very front
    count: int = 1
    width: Optional[float] = None
    height: Optional[float] = None


class MergeRequest(BaseModel):
    doc_ids: List[str] = Field(description="Documents to append, in order")
    insert_after: Optional[int] = Field(
        default=None, description="Insert after this page; None appends at the end"
    )


class SplitRequest(BaseModel):
    mode: Literal["every_n", "at_pages", "ranges"] = "every_n"
    every_n: int = 1
    at_pages: List[int] = []
    ranges: List[str] = []


class CropRequest(BaseModel):
    pages: List[int]
    rect: Rect
    unit: Literal["pdf", "ratio"] = "pdf"


class ExtractRequest(BaseModel):
    pages: List[int]
    filename: Optional[str] = None


# --------------------------------------------------------------------------
# Annotations
# --------------------------------------------------------------------------

AnnotKind = Literal[
    "highlight", "underline", "strikeout", "squiggly",
    "note", "ink", "rect", "circle", "line", "arrow",
    "freetext", "stamp_text",
]


class AnnotationSpec(BaseModel):
    kind: AnnotKind
    page: int
    rect: Optional[Rect] = None
    quads: List[Rect] = Field(default=[], description="Text-markup quads in PDF coords")
    points: List[List[Tuple[float, float]]] = Field(
        default=[], description="Ink strokes: list of polylines"
    )
    text: Optional[str] = None
    author: Optional[str] = None
    colour: str = "#ffd400"
    fill: Optional[str] = None
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    stroke_width: float = 1.5
    font_size: float = 11.0
    font: str = "helv"
    icon: str = "Note"


class AnnotationRef(BaseModel):
    page: int
    index: int
    kind: str
    rect: Optional[Rect] = None
    text: Optional[str] = None
    author: Optional[str] = None
    colour: Optional[str] = None


class DeleteAnnotationRequest(BaseModel):
    page: int
    indices: List[int]


# --------------------------------------------------------------------------
# Text / content editing
# --------------------------------------------------------------------------

class TextSpanInfo(BaseModel):
    page: int
    rect: Rect
    text: str
    font: str
    size: float
    colour: str
    flags: int = 0
    bold: bool = False
    italic: bool = False


class EditTextRequest(BaseModel):
    page: int
    rect: Rect
    new_text: str
    font: Optional[str] = None
    size: Optional[float] = None
    colour: Optional[str] = None
    align: Literal["left", "center", "right"] = "left"
    background: Optional[str] = Field(
        default=None, description="Fill colour painted before the new text; auto-sampled if null"
    )


class DeleteContentRequest(BaseModel):
    page: int
    rect: Rect
    background: Optional[str] = None


class MoveContentRequest(BaseModel):
    page: int
    rect: Rect
    dx: float
    dy: float
    background: Optional[str] = None


class ReplaceImageRequest(BaseModel):
    page: int
    xref: Optional[int] = None
    rect: Optional[Rect] = None
    asset_id: str


class AddImageRequest(BaseModel):
    page: int
    rect: Rect
    asset_id: str
    opacity: float = 1.0
    keep_aspect: bool = True


class FindReplaceRequest(BaseModel):
    find: str
    replace: str
    pages: PageSelection = PageSelection()
    match_case: bool = False
    whole_word: bool = False
    limit: int = 500


# --------------------------------------------------------------------------
# Forms, signatures, redaction, watermark, Bates
# --------------------------------------------------------------------------

class FormField(BaseModel):
    page: int
    name: str
    field_type: str
    value: Any = None
    options: List[str] = []
    rect: Optional[Rect] = None
    read_only: bool = False
    required: bool = False
    max_len: Optional[int] = None


class FillFormRequest(BaseModel):
    values: Dict[str, Any]
    flatten: bool = False


class SignatureRequest(BaseModel):
    page: int
    rect: Rect
    asset_id: Optional[str] = None
    strokes: List[List[Tuple[float, float]]] = []
    colour: str = "#101010"
    stroke_width: float = 1.8
    flatten: bool = True


class RedactionSpec(BaseModel):
    page: int
    rect: Rect
    fill: str = "#000000"
    overlay_text: Optional[str] = None
    text_colour: str = "#ffffff"


class ApplyRedactionsRequest(BaseModel):
    redactions: List[RedactionSpec]
    remove_images: bool = True
    scrub_metadata: bool = True


class RedactSearchRequest(BaseModel):
    patterns: List[str] = Field(description="Literal strings or /regex/ forms")
    pages: PageSelection = PageSelection()
    match_case: bool = False
    apply_now: bool = False


class WatermarkRequest(BaseModel):
    text: Optional[str] = None
    asset_id: Optional[str] = None
    pages: PageSelection = PageSelection()
    opacity: float = Field(default=0.15, ge=0.0, le=1.0)
    rotation: float = 45.0
    font_size: float = 60.0
    colour: str = "#b00020"
    scale: float = Field(default=0.6, description="Fraction of page width for image watermarks")
    position: Literal["center", "tile", "top", "bottom"] = "center"


class BatesRequest(BaseModel):
    prefix: str = ""
    suffix: str = ""
    start: int = 1
    digits: int = 6
    pages: PageSelection = PageSelection()
    position: Literal[
        "bottom-right", "bottom-left", "bottom-center", "top-right", "top-left", "top-center"
    ] = "bottom-right"
    font_size: float = 9.0
    colour: str = "#333333"
    margin: float = 24.0


# --------------------------------------------------------------------------
# Logo stamping
# --------------------------------------------------------------------------

class LogoConfig(BaseModel):
    width_pt: float = Field(default=120.0, description="Target logo width in PDF points")
    height_pt: float = Field(default=40.0, description="Target logo height in PDF points")
    maintain_aspect_ratio: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)

    margin_bottom: float = 24.0
    margin_side: float = 28.0
    content_padding: float = Field(default=10.0, description="Clearance from text/drawings/images")

    search_band_ratio: float = Field(
        default=0.35, description="Fraction of page height from the bottom to search"
    )
    strategy_priority: List[str] = Field(
        default=["bottom-right", "bottom-left", "bottom-center", "best-fit"]
    )
    fallback_behavior: Literal["shrink_to_fit", "subtle_overlay", "skip_page"] = "shrink_to_fit"
    min_scale: float = Field(default=0.5, ge=0.2, le=1.0)

    page_selection: Literal["all", "first", "last", "all_except_first", "custom"] = "all"
    custom_pages: Optional[str] = None


class PagePlacement(BaseModel):
    page_number: int
    page_width: float
    page_height: float
    placed: bool
    strategy_used: Optional[str] = None
    rect: Optional[Rect] = None
    scale: float = 1.0
    opacity: float = 1.0
    message: str = "Success"
    is_manual_override: bool = False
    obstacles: List[ObstacleBox] = []


class DocumentAnalysisResult(BaseModel):
    doc_id: str
    filename: str
    total_pages: int
    page_placements: List[PagePlacement]


class BatchStampRequest(BaseModel):
    doc_ids: List[str]
    logo_id: str
    config: LogoConfig = LogoConfig()
    manual_overrides: Dict[str, Dict[int, Rect]] = {}
    apply_in_place: bool = Field(
        default=False, description="Write into the open documents instead of producing a ZIP"
    )


class BatchStampResult(BaseModel):
    job_id: str
    total_documents: int
    total_pages_stamped: int
    documents: List[DocumentAnalysisResult]
    download_url: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

class OcrBlock(BaseModel):
    text: str
    rect: Optional[Rect] = None
    confidence: Optional[float] = None
    kind: str = "text"  # text | title | table | figure | header | footer


class OcrPageResult(BaseModel):
    page_number: int
    engine: str
    text: str = ""
    markdown: str = ""
    blocks: List[OcrBlock] = []
    width: float = 0.0
    height: float = 0.0
    duration_ms: int = 0
    warning: Optional[str] = None


class OcrRequest(BaseModel):
    pages: PageSelection = PageSelection()
    engine: Optional[str] = Field(default=None, description="None/'auto' selects the best available")
    mode: Literal["markdown", "plain", "grounded"] = "markdown"
    dpi: Optional[int] = None
    region: Optional[Rect] = None
    region_page: Optional[int] = None
    prompt: Optional[str] = Field(default=None, description="Override the engine prompt")
    force: bool = Field(
        default=False, description="OCR even when the page already has a usable text layer"
    )


class OcrEngineInfo(BaseModel):
    name: str
    label: str
    available: bool
    priority: int
    device: Optional[str] = None
    detail: str = ""
    install_hint: str = ""
    supports_layout: bool = False
    supports_markdown: bool = False


class OcrCapabilities(BaseModel):
    engines: List[OcrEngineInfo]
    selected: Optional[str] = None
    gpu_available: bool = False
    gpu_name: Optional[str] = None
    torch_version: Optional[str] = None


class SearchablePdfRequest(BaseModel):
    pages: PageSelection = PageSelection()
    engine: Optional[str] = None
    dpi: Optional[int] = None


# --------------------------------------------------------------------------
# Purchase orders
# --------------------------------------------------------------------------

class POLineItem(BaseModel):
    line_no: Optional[int] = None
    part_code: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[str] = None
    total_price: Optional[str] = None
    discount: Optional[str] = None
    tax: Optional[str] = None
    due_date: Optional[str] = None
    extra: Dict[str, str] = {}
    page: int = 1
    rect: Optional[Rect] = None
    confidence: float = 1.0


class POHeader(BaseModel):
    po_number: Optional[str] = None
    order_date: Optional[str] = None
    required_date: Optional[str] = None
    supplier: Optional[str] = None
    ship_to: Optional[str] = None
    buyer: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[str] = None
    tax: Optional[str] = None
    total: Optional[str] = None
    reference: Optional[str] = None
    extra: Dict[str, str] = {}


class PurchaseOrderResult(BaseModel):
    doc_id: str
    filename: str
    header: POHeader = POHeader()
    columns: List[str] = []
    line_items: List[POLineItem] = []
    template_used: Optional[str] = None
    source: str = "text"  # text | ocr
    engine: Optional[str] = None
    warnings: List[str] = []
    raw_text: str = ""


class ColumnMapping(BaseModel):
    """Maps a detected table column (by x-range) onto a canonical PO field."""

    field: str
    header_text: Optional[str] = None
    x0: float
    x1: float


class POTemplate(BaseModel):
    template_id: str
    name: str
    supplier_match: List[str] = Field(
        default=[], description="Substrings that identify this supplier's PO"
    )
    columns: List[ColumnMapping] = []
    header_patterns: Dict[str, str] = Field(
        default={}, description="Canonical field -> regex with one capture group"
    )
    table_top_marker: Optional[str] = None
    table_bottom_marker: Optional[str] = None
    created_at: Optional[str] = None


class POExtractRequest(BaseModel):
    pages: PageSelection = PageSelection()
    template_id: Optional[str] = None
    force_ocr: bool = False
    engine: Optional[str] = None


# --------------------------------------------------------------------------
# Panel extraction (Dynalite / Smart Home Works)
# --------------------------------------------------------------------------

class PanelLabel(BaseModel):
    text: str
    include: bool = True
    copied: bool = False
    rect: Optional[Rect] = None


class PanelEntry(BaseModel):
    panel_id: str
    name: str
    rows: List[List[PanelLabel]] = []
    page: int = 1
    style: str = "dynalite"  # dynalite | smart | ocr
    done: bool = False
    error: Optional[str] = None
    source_file: str = ""
    config_key: str = ""


class PanelExtractResult(BaseModel):
    doc_id: str
    filename: str
    style: str
    entries: List[PanelEntry] = []
    used_ocr: bool = False
    engine: Optional[str] = None
    warnings: List[str] = []


class PanelExtractRequest(BaseModel):
    force_ocr: bool = False
    engine: Optional[str] = None
    style: Optional[Literal["auto", "dynalite", "smart"]] = "auto"


class PanelExportRequest(BaseModel):
    entries: List[PanelEntry]
    job_name: str = "panel-job"
    fmt: Literal["csv", "txt", "xlsx"] = "csv"


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------

class AssetInfo(BaseModel):
    asset_id: str
    filename: str
    width: int
    height: int
    aspect_ratio: float
    kind: str = "image"


class OperationResult(BaseModel):
    status: str = "success"
    message: str = ""
    document: Optional[DocumentInfo] = None
    data: Optional[Dict[str, Any]] = None
