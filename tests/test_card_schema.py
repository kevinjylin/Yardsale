"""Yardsale's card models (yardsale/card_schema.py) against the rules ASI:One's web app applies."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat import cards as uagents_cards

from tests import sim
from yardsale.card_schema import (
    AccordionItem,
    AccordionNode,
    BadgeNode,
    ButtonAction,
    ButtonNode,
    CardPayload,
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    ChoiceGridChoice,
    ChoiceGridNode,
    CtaAction,
    CustomCardPayload,
    DetailCardPayload,
    DetailSummaryRow,
    DividerNode,
    FormCardPayload,
    FormField,
    FormFieldOption,
    GroupNode,
    HeadingNode,
    ImageNode,
    ImageSpec,
    InputNode,
    InputOption,
    LabelIcon,
    ListItem,
    ListNode,
    SectionNode,
    SelectionValue,
    TextNode,
    VideoNode,
    create_card_content,
    extract_card,
)

PIC = "https://example.test/bike.jpg"
MENU = CtaAction(label="Main menu", selection={"action": "menu"})


def tap(**selection: SelectionValue) -> ButtonAction:
    return ButtonAction(selection=selection)


def carousel() -> CarouselCardPayload:
    return CarouselCardPayload(
        title="On other sites",
        subtitle="Best first",
        style="slide",
        root_cta=MENU,
        items=[
            CarouselItem(
                id="1",
                title="Trek FX 2",
                subtitle="$180 on Craigslist",
                image=[PIC, ImageSpec(src=PIC, alt="A bike", aspect_ratio="16:9")],
                badges=[CarouselBadge(label="42% under", variant="success")],
                secondary_text="$180",
                primary_cta=CtaAction(label="View", selection={"action": "deal", "n": 1}),
            )
        ],
    )


def detail() -> DetailCardPayload:
    return DetailCardPayload(
        title="Trek FX 2",
        hero_image=ImageSpec(src=PIC, alt="A bike"),
        summary_rows=[DetailSummaryRow(label="Asking", value="$340")],
        ctas=[CtaAction(label="Approve", selection={"action": "approve"}, primary=True), MENU],
    )


def form() -> FormCardPayload:
    return FormCardPayload(
        title="Your school email",
        fields=[
            FormField(name="email", kind="email", label="School email", required=True),
            FormField(
                name="school",
                kind="select",
                label="School",
                options=[FormFieldOption(value="ucr", label="UCR")],
                default="ucr",
            ),
        ],
        submit_cta=CtaAction(label="Join", selection={"action": "student_email"}, primary=True),
        cancel_cta=CtaAction(label="Not now", selection={"action": "menu"}),
    )


def custom() -> CustomCardPayload:
    """One of every element."""
    return CustomCardPayload(
        root=SectionNode(
            title="Home",
            subtitle="What next?",
            children=[
                HeadingNode(value="Yardsale", level=1),
                TextNode(value="Sell or buy", style="muted"),
                ImageNode(src=PIC, alt="Art", aspect_ratio="16:9"),
                VideoNode(src="https://example.test/demo.mp4"),
                BadgeNode(label="2 for sale", variant="info"),
                DividerNode(),
                GroupNode(
                    direction="row",
                    gap=2,
                    style="inset",
                    children=[
                        ButtonNode(
                            label="Sell something",
                            image=ImageSpec(src=PIC, alt="Sell", aspect_ratio="1:1"),
                            primary=True,
                            action=tap(action="sell"),
                        ),
                        ButtonNode(
                            label=LabelIcon(label="Open on eBay", src=PIC),
                            action=ButtonAction(redirect="https://www.ebay.com/itm/1"),
                        ),
                        ButtonNode(
                            image=ImageSpec(src=PIC, alt="Find a deal"),
                            action=ButtonAction(
                                selection={"action": "buy"},
                                redirect="https://example.test/deal",
                                bypass_required_validation=True,
                            ),
                        ),
                    ],
                ),
                InputNode(
                    name="max",
                    kind="number",
                    label="Your max",
                    minimum=1,
                    maximum=5000,
                    description="In dollars",
                ),
                InputNode(
                    name="what",
                    kind="select",
                    label="What",
                    options=[InputOption(value="bike", label="Bike")],
                ),
                ListNode(items=[ListItem(children=[TextNode(value="#1")], action=tap(n=1))]),
                ChoiceGridNode(
                    name="pick",
                    multi=True,
                    layout="list",
                    choices=[
                        ChoiceGridChoice(
                            value="1",
                            label="Bike",
                            image=PIC,
                            secondary_text="$180",
                            badge=CarouselBadge(label="New"),
                        )
                    ],
                ),
                AccordionNode(
                    items=[
                        AccordionItem(
                            label="Why this price",
                            subtitle="13 sales",
                            badge=CarouselBadge(label="High", variant="success"),
                            default_open=True,
                            children=[TextNode(value="Median of 13 eBay sales")],
                        )
                    ]
                ),
            ],
        )
    )


@pytest.mark.parametrize(
    ("make", "kind"),
    [(carousel, "carousel"), (detail, "detail"), (form, "form"), (custom, "custom")],
)
def test_every_kind_round_trips(make: Any, kind: str) -> None:
    payload: CardPayload = make()
    card_id = uuid4()
    block = create_card_content(
        payload, card_id=card_id, preferred_drawer_width_px=520, is_terminal=True
    )
    assert block.metadata["card_kind"] == kind
    assert block.metadata["requires_card_interaction"] == "true"
    card = extract_card(block)
    assert card is not None
    assert card.payload == payload
    assert card.card_id == card_id
    assert card.is_terminal and card.preferred_drawer_width_px == 520
    assert not card.resubmittable


def test_resubmittable_goes_in_the_metadata() -> None:
    plain = create_card_content(detail())
    assert "resubmittable" not in plain.metadata
    again = create_card_content(detail(), resubmittable=True)
    assert again.metadata["resubmittable"] == "true"
    card = extract_card(again)
    assert card is not None and card.resubmittable


def test_element_types_are_written_out() -> None:
    """`type` has a default, so it's easy to leave out, but the app needs it in the JSON."""
    payload = json.loads(create_card_content(custom()).metadata["card_payload"])
    assert payload["root"]["type"] == "section"
    assert [child["type"] for child in payload["root"]["children"]][:3] == [
        "heading",
        "text",
        "image",
    ]
    assert "null" not in create_card_content(custom()).metadata["card_payload"]


def test_cards_made_with_uagents_core_still_read() -> None:
    """What uagents_core's models allow is a subset of what the app reads."""
    old = uagents_cards.CustomCardPayload(
        root=uagents_cards.GroupNode(
            type="group",
            direction="column",
            children=[
                uagents_cards.TextNode(type="text", value="Hi"),
                uagents_cards.ButtonNode(
                    type="button", label="Go", action=uagents_cards.ButtonAction(selection={"a": 1})
                ),
            ],
        )
    )
    card = extract_card(uagents_cards.create_card_content(old, card_id=uuid4()))
    assert card is not None and isinstance(card.payload, CustomCardPayload)
    assert json.loads(card.payload.model_dump_json(exclude_none=True)) == json.loads(
        old.model_dump_json(exclude_none=True)
    )


def test_blocks_that_are_not_cards_the_app_shows() -> None:
    assert extract_card(MetadataContent(metadata={"attachments": "true"})) is None
    good = create_card_content(detail()).metadata
    for change in (
        {"card_kind": "review"},  # Yardsale doesn't send review cards
        {"card_kind": "poster"},
        {"requires_card_interaction": "false"},
        {"card_protocol_version": "2"},
        {"card_payload": "{not json"},
        {"card_id": "not-a-uuid"},
        {"preferred_drawer_width_px": "wide"},
    ):
        assert extract_card(MetadataContent(metadata={**good, **change})) is None, change


def test_a_payload_with_an_unknown_field_is_not_shown() -> None:
    payload = json.loads(create_card_content(carousel()).metadata["card_payload"])
    payload["items"][0]["color"] = "red"
    block = create_card_content(carousel()).metadata | {"card_payload": json.dumps(payload)}
    assert extract_card(MetadataContent(metadata=block)) is None
    with pytest.raises(ValidationError):
        TextNode.model_validate({"type": "text", "value": "x", "color": "red"})


@pytest.mark.parametrize("src", ["ftp://example.test/a.jpg", "/assets/a.jpg", "https://", ""])
def test_pictures_must_be_web_links(src: str) -> None:
    with pytest.raises(ValidationError):
        ImageNode(src=src)
    with pytest.raises(ValidationError):
        ImageSpec(src=src)
    with pytest.raises(ValidationError):
        CarouselItem(id="1", title="x", image=src, primary_cta=MENU)


@pytest.mark.parametrize("ratio", ["16/9", "16:9.5", "wide", ":9", "16:", "١٦:٩"])
def test_aspect_ratios_are_whole_numbers(ratio: str) -> None:
    with pytest.raises(ValidationError):
        ImageNode(src=PIC, aspect_ratio=ratio)
    assert ImageNode(src=PIC, aspect_ratio="4:3").aspect_ratio == "4:3"


def test_a_button_action_needs_something_to_do() -> None:
    with pytest.raises(ValidationError):
        ButtonAction(selection={})
    with pytest.raises(ValidationError):
        ButtonAction()
    with pytest.raises(ValidationError):
        ButtonAction(selection={}, redirect=PIC)  # an empty selection fails even with a link
    with pytest.raises(ValidationError):
        ButtonAction(redirect="mailto:someone@example.test")


def test_a_button_needs_a_label_or_a_picture() -> None:
    with pytest.raises(ValidationError):
        ButtonNode(action=tap(action="menu"))
    with pytest.raises(ValidationError):
        ButtonNode(label="", action=tap(action="menu"))
    with pytest.raises(ValidationError):
        ButtonNode(label=LabelIcon(label="", src=PIC), action=tap(action="menu"))
    # A picture alone needs words for screen readers.
    with pytest.raises(ValidationError):
        ButtonNode(image=ImageSpec(src=PIC), action=tap(action="menu"))
    with pytest.raises(ValidationError):
        ButtonNode(image=ImageSpec(src=PIC, alt=""), action=tap(action="menu"))


def test_picture_tiles_and_link_buttons_are_fine() -> None:
    tile = ButtonNode(
        label="Sell something", image=ImageSpec(src=PIC, aspect_ratio="1:1"), action=tap(a="b")
    )
    link = ButtonNode(label="Open", action=ButtonAction(redirect="https://www.ebay.com/itm/1"))
    picture = ButtonNode(image=ImageSpec(src=PIC, alt="Sell"), action=tap(a="b"))
    card = extract_card(
        create_card_content(
            CustomCardPayload(root=GroupNode(direction="row", children=[tile, link, picture]))
        )
    )
    assert card is not None and isinstance(card.payload, CustomCardPayload)
    assert card.payload.root == GroupNode(direction="row", children=[tile, link, picture])


def nested(levels: int) -> CustomCardPayload:
    node: Any = TextNode(value="deep")
    for _ in range(levels - 1):
        node = GroupNode(direction="column", children=[node])
    return CustomCardPayload(root=node)


def test_trees_go_at_most_eight_deep() -> None:
    assert nested(8).root.type == "group"
    with pytest.raises(ValidationError):
        nested(9)
    # Lists and accordions count a level each, like groups.
    seven, eight = nested(7).root, nested(8).root
    CustomCardPayload(root=ListNode(items=[ListItem(children=[seven])]))
    CustomCardPayload(root=AccordionNode(items=[AccordionItem(label="x", children=[seven])]))
    with pytest.raises(ValidationError):
        CustomCardPayload(root=ListNode(items=[ListItem(children=[eight])]))
    with pytest.raises(ValidationError):
        CustomCardPayload(root=AccordionNode(items=[AccordionItem(label="x", children=[eight])]))


@pytest.mark.parametrize("name", ["__proto__", "constructor", "prototype"])
def test_reserved_names_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        InputNode(name=name, kind="text", label="x")
    with pytest.raises(ValidationError):
        FormField(name=name, kind="text", label="x")
    with pytest.raises(ValidationError):
        ChoiceGridNode(name=name, choices=[ChoiceGridChoice(value="1", label="One")])


def test_a_select_needs_options() -> None:
    with pytest.raises(ValidationError):
        InputNode(name="pick", kind="select", label="Pick one")
    with pytest.raises(ValidationError):
        InputNode(name="pick", kind="select", label="Pick one", options=[])
    with pytest.raises(ValidationError):
        FormField(name="pick", kind="select", label="Pick one")


def test_empty_lists_are_refused() -> None:
    with pytest.raises(ValidationError):
        CarouselCardPayload(items=[])
    with pytest.raises(ValidationError):
        DetailCardPayload(title="x", ctas=[])
    with pytest.raises(ValidationError):
        FormCardPayload(fields=[], submit_cta=MENU)
    with pytest.raises(ValidationError):
        GroupNode(direction="row", children=[])
    with pytest.raises(ValidationError):
        CarouselItem(id="1", title="x", image=[], primary_cta=MENU)
    with pytest.raises(ValidationError):
        GroupNode(direction="row", gap=0, children=[TextNode(value="x")])


def test_the_test_helpers_read_the_new_fields() -> None:
    """tests/sim.py reads cards the way a person sees them."""
    page = extract_card(create_card_content(custom()))
    assert page is not None
    text = sim.card_text(page).splitlines()
    assert ["Home", "What next?", "Yardsale", "Sell or buy", "2 for sale"] == text[:5]
    assert "Open on eBay" in text and "Find a deal" not in text  # a picture shows no words
    assert ["Bike", "$180", "New"] == text[text.index("Bike") :][:3]
    assert ["Why this price", "13 sales", "High", "Median of 13 eBay sales"] == text[-4:]
    assert sim.card_buttons(page) == [
        ("Sell something", {"action": "sell"}),
        ("Find a deal", {"action": "buy"}),  # a picture-only button goes by its alt text
        ("", {"n": 1}),
    ]
    assert sim.card_links(page) == [
        ("Open on eBay", "https://www.ebay.com/itm/1"),
        ("Find a deal", "https://example.test/deal"),
    ]
    deals = extract_card(create_card_content(carousel()))
    assert deals is not None
    assert sim.card_text(deals).splitlines()[-1] == "Main menu"
    assert sim.card_buttons(deals)[-1] == ("Main menu", {"action": "menu"})
    assert sim.card_links(deals) == []
