"""Interactive Card models that match what ASI:One's web app reads.

These mirror the card schemas in asi1.ai's own code, as read from its web app on October 25,
2026. The models in uagents_core 0.4.12 (the newest on PyPI) are narrower: they reject fields
the app shows, such as picture buttons, link buttons, inset groups, accordions, a carousel's
own button, and the `resubmittable` flag. So Yardsale builds and reads cards with these.

Every model forbids unknown fields, as the app does. The app doesn't show a card it rejects at
all (the message's text still shows), so a card that fails here would fail there too.

Only the kinds Yardsale sends are here: carousel, detail, form, and custom. Responses to a tap
are unchanged; use uagents_core's `create_card_response_content` and `extract_card_response`.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    CARD_PROTOCOL_VERSION,
    META_CARD_ID,
    META_CARD_KIND,
    META_CARD_PAYLOAD,
    META_CARD_PROTOCOL_VERSION,
    META_IS_TERMINAL,
    META_PREFERRED_DRAWER_WIDTH_PX,
    META_REQUIRES_CARD_INTERACTION,
)

_logger = logging.getLogger(__name__)

# Without this, the app locks a card after its first tap.
META_RESUBMITTABLE = "resubmittable"

# The app counts a section or group as one level plus its deepest child, and a list or
# accordion as one level plus its items' deepest child.
MAX_ELEMENT_TREE_DEPTH = 8

# Input and choice names the app refuses.
RESERVED_NAMES = frozenset({"__proto__", "constructor", "prototype"})

Url = Annotated[str, StringConstraints(pattern=r"^https?://.+")]
AspectRatio = Annotated[str, StringConstraints(pattern=r"^[0-9]+:[0-9]+$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Variant = Literal["info", "success", "warning"]
InputKind = Literal["text", "number", "email", "select", "checkbox"]

SelectionValue = str | int | float | bool
Selection = dict[str, SelectionValue]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_name(name: str) -> None:
    if name in RESERVED_NAMES:
        raise ValueError(f"{name!r} is a reserved name")


# --- Shared pieces ---------------------------------------------------------------------------


class ImageSpec(_Strict):
    src: Url
    alt: str | None = None
    aspect_ratio: AspectRatio | None = None


# A picture is a link, or a link with alt text and a shape.
Image = Url | ImageSpec
ImageList = Annotated[list[Image], Field(min_length=1)]


class CarouselBadge(_Strict):
    """A badge on a carousel item, a choice, or an accordion item."""

    label: str
    variant: Variant | None = None


class CtaAction(_Strict):
    """A carousel, detail, or form button. It can only send a selection, not open a link."""

    label: str
    selection: Selection
    primary: bool = False


# --- Carousel --------------------------------------------------------------------------------


class CarouselItem(_Strict):
    id: str
    logo: str | None = None
    image: Image | ImageList | None = None
    title: str
    subtitle: str | None = None
    badges: list[CarouselBadge] | None = None
    secondary_text: str | None = None
    primary_cta: CtaAction


class CarouselCardPayload(_Strict):
    title: str | None = None
    subtitle: str | None = None
    style: Literal["scroll", "slide"] | None = None
    # A full-width button under the carousel.
    root_cta: CtaAction | None = None
    items: list[CarouselItem] = Field(min_length=1)


# --- Detail ----------------------------------------------------------------------------------


class DetailSummaryRow(_Strict):
    label: str
    value: str


class DetailChoiceExpanded(_Strict):
    """What a sub-option shows once picked. The app's types for these weren't checked closely,
    and Yardsale doesn't send sub-options."""

    image: Image | None = None
    content: str | None = None
    additional_data: dict[str, SelectionValue] | None = None


class DetailSubOptionChoice(_Strict):
    value: str
    label: str
    secondary_text: str | None = None
    expanded: DetailChoiceExpanded | None = None


class DetailSubOptions(_Strict):
    name: str
    kind: Literal["radio", "select"]
    label: str
    choices: list[DetailSubOptionChoice] = Field(min_length=1)


class DetailCardPayload(_Strict):
    title: str
    hero_image: Image | ImageList | None = None
    summary_rows: list[DetailSummaryRow] | None = None
    sub_options: DetailSubOptions | None = None
    ctas: list[CtaAction] = Field(min_length=1)


# --- Form ------------------------------------------------------------------------------------


class FormFieldOption(_Strict):
    value: str
    label: str


class FormField(_Strict):
    name: str
    kind: InputKind
    label: str
    required: bool = False
    options: list[FormFieldOption] | None = None
    placeholder: str | None = None
    default: SelectionValue | None = None
    description: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    multiline: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_name(self.name)
        if self.kind == "select" and not self.options:
            raise ValueError("a select field needs options")
        return self


class FormCardPayload(_Strict):
    title: str | None = None
    fields: list[FormField] = Field(min_length=1)
    submit_cta: CtaAction
    cancel_cta: CtaAction | None = None


# --- Custom cards: a tree of elements --------------------------------------------------------


class TextNode(_Strict):
    type: Literal["text"] = "text"
    value: str
    style: Literal["body", "muted", "emphasis"] | None = None


class HeadingNode(_Strict):
    type: Literal["heading"] = "heading"
    value: str
    level: Literal[1, 2, 3] = 2


class ImageNode(_Strict):
    type: Literal["image"] = "image"
    src: Url
    alt: str | None = None
    aspect_ratio: AspectRatio | None = None


class VideoNode(_Strict):
    type: Literal["video"] = "video"
    src: Url
    alt: str | None = None
    aspect_ratio: AspectRatio | None = None


class BadgeNode(_Strict):
    type: Literal["badge"] = "badge"
    label: str
    variant: Variant | None = None


class DividerNode(_Strict):
    type: Literal["divider"] = "divider"


class SectionNode(_Strict):
    type: Literal["section"] = "section"
    title: str | None = None
    subtitle: str | None = None
    children: list[ElementTreeNode] = Field(min_length=1)


class GroupNode(_Strict):
    """A row or column. In a row, only buttons, images, videos, inputs, text, and badges sit
    side by side; anything else takes a whole line."""

    type: Literal["group"] = "group"
    direction: Literal["row", "column"]
    gap: Annotated[int, Field(strict=True, ge=1)] | None = None
    # "inset" draws a rounded, lightly shaded box around the children.
    style: Literal["plain", "inset"] | None = None
    children: list[ElementTreeNode] = Field(min_length=1)


class ButtonAction(_Strict):
    """What a tap does: send a selection to the agent, open a link in a new tab, or both. A
    link-only button sends the agent nothing."""

    selection: Selection | None = None
    redirect: Url | None = None
    bypass_required_validation: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.selection is None and self.redirect is None:
            raise ValueError("a button action needs a selection or a redirect")
        if self.selection is not None and not self.selection:
            raise ValueError("a selection needs at least one key")
        return self


class LabelIcon(_Strict):
    """A button label with a small icon beside it."""

    label: NonEmpty
    src: Url
    alt: str | None = None


class ButtonNode(_Strict):
    """A button. With an image it's a tile: the picture on top, the label under it."""

    type: Literal["button"] = "button"
    label: NonEmpty | LabelIcon | None = None
    image: ImageSpec | None = None
    primary: bool = False
    action: ButtonAction

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.label is None:
            if self.image is None:
                raise ValueError("a button needs a label or an image")
            if not self.image.alt:
                raise ValueError("a picture-only button needs alt text")
        return self


class InputOption(_Strict):
    value: str
    label: str


class InputNode(_Strict):
    type: Literal["input"] = "input"
    name: str
    kind: InputKind
    label: str
    required: bool = False
    options: list[InputOption] | None = None
    placeholder: str | None = None
    default: SelectionValue | None = None
    description: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    multiline: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_name(self.name)
        if self.kind == "select" and not self.options:
            raise ValueError("a select input needs options")
        return self


class ListItem(_Strict):
    children: list[ElementTreeNode] = Field(min_length=1)
    action: ButtonAction | None = None


class ListNode(_Strict):
    type: Literal["list"] = "list"
    items: list[ListItem] = Field(min_length=1)


class ChoiceGridChoice(_Strict):
    value: str
    label: str
    image: Image | None = None
    secondary_text: str | None = None
    badge: CarouselBadge | None = None


class ChoiceGridNode(_Strict):
    """Choices to pick from. A tap only selects; a button sends the choice."""

    type: Literal["choice_grid"] = "choice_grid"
    name: str
    choices: list[ChoiceGridChoice] = Field(min_length=1)
    multi: bool = False
    layout: Literal["grid", "list"] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_name(self.name)
        return self


class AccordionItem(_Strict):
    label: NonEmpty
    subtitle: str | None = None
    badge: CarouselBadge | None = None
    default_open: bool | None = None
    children: list[ElementTreeNode] = Field(min_length=1)


class AccordionNode(_Strict):
    type: Literal["accordion"] = "accordion"
    items: list[AccordionItem] = Field(min_length=1)


ElementTreeNode = Annotated[
    TextNode
    | HeadingNode
    | ImageNode
    | VideoNode
    | BadgeNode
    | DividerNode
    | SectionNode
    | GroupNode
    | ButtonNode
    | InputNode
    | ListNode
    | ChoiceGridNode
    | AccordionNode,
    Field(discriminator="type"),
]


def tree_depth(node: BaseModel) -> int:
    """How deep an element tree goes, counted the way the app counts it."""
    if isinstance(node, SectionNode | GroupNode):
        return 1 + max(tree_depth(child) for child in node.children)
    if isinstance(node, ListNode | AccordionNode):
        return 1 + max(tree_depth(child) for item in node.items for child in item.children)
    return 1


class CustomCardPayload(_Strict):
    root: ElementTreeNode

    @model_validator(mode="after")
    def _check(self) -> Self:
        depth = tree_depth(self.root)
        if depth > MAX_ELEMENT_TREE_DEPTH:
            raise ValueError(f"the element tree is {depth} deep, over {MAX_ELEMENT_TREE_DEPTH}")
        return self


SectionNode.model_rebuild()
GroupNode.model_rebuild()
ListItem.model_rebuild()
AccordionItem.model_rebuild()
CustomCardPayload.model_rebuild()


# --- Cards in chat messages ------------------------------------------------------------------

CardPayload = CarouselCardPayload | DetailCardPayload | FormCardPayload | CustomCardPayload

_KINDS: dict[str, type[CardPayload]] = {
    "carousel": CarouselCardPayload,
    "detail": DetailCardPayload,
    "form": FormCardPayload,
    "custom": CustomCardPayload,
}


def card_kind(payload: CardPayload) -> str:
    """The `card_kind` the app reads for this payload."""
    return next(kind for kind, model in _KINDS.items() if type(payload) is model)


class Card(_Strict):
    payload: CardPayload
    card_id: UUID | None = None
    is_terminal: bool = False
    preferred_drawer_width_px: int | None = None
    # Whether the card stays usable after a tap.
    resubmittable: bool = False


def create_card_content(
    payload: CardPayload,
    *,
    card_id: UUID | None = None,
    is_terminal: bool = False,
    preferred_drawer_width_px: int | None = None,
    resubmittable: bool = False,
) -> MetadataContent:
    """A card as a MetadataContent block for a ChatMessage, as uagents_core writes one, plus
    `resubmittable` when set."""
    metadata = {
        META_CARD_PROTOCOL_VERSION: CARD_PROTOCOL_VERSION,
        META_REQUIRES_CARD_INTERACTION: "true",
        META_CARD_KIND: card_kind(payload),
        META_CARD_PAYLOAD: payload.model_dump_json(exclude_none=True),
    }
    if card_id is not None:
        metadata[META_CARD_ID] = str(card_id)
    if is_terminal:
        metadata[META_IS_TERMINAL] = "true"
    if preferred_drawer_width_px is not None:
        metadata[META_PREFERRED_DRAWER_WIDTH_PX] = str(preferred_drawer_width_px)
    if resubmittable:
        metadata[META_RESUBMITTABLE] = "true"
    return MetadataContent(metadata=metadata)


def extract_card(content: MetadataContent) -> Card | None:
    """The card a MetadataContent block carries, or None if it carries none the app would show:
    not a card, a kind Yardsale doesn't send (review), or a payload that fails the schema."""
    metadata = content.metadata
    if metadata.get(META_CARD_PROTOCOL_VERSION) != CARD_PROTOCOL_VERSION:
        return None
    if metadata.get(META_REQUIRES_CARD_INTERACTION) != "true":
        return None
    model = _KINDS.get(metadata.get(META_CARD_KIND, ""))
    raw = metadata.get(META_CARD_PAYLOAD)
    if model is None or raw is None:
        return None
    try:
        payload = model.model_validate_json(raw)
    except ValidationError as err:
        # Field paths only: the values can be a seller's prices or what someone typed.
        where = sorted({".".join(str(part) for part in e["loc"]) for e in err.errors()})
        _logger.warning("card payload failed the schema at %s", ", ".join(where))
        return None
    try:
        width_raw = metadata.get(META_PREFERRED_DRAWER_WIDTH_PX)
        width = int(width_raw) if width_raw is not None else None
        id_raw = metadata.get(META_CARD_ID)
        card_id = UUID(id_raw) if id_raw is not None else None
    except ValueError:
        _logger.warning("card has a bad card_id or preferred_drawer_width_px")
        return None
    return Card(
        payload=payload,
        card_id=card_id,
        is_terminal=metadata.get(META_IS_TERMINAL) == "true",
        preferred_drawer_width_px=width,
        resubmittable=metadata.get(META_RESUBMITTABLE) == "true",
    )
