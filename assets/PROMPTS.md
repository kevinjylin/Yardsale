# Card art and prompts

Every ASI:One card shows one of these images. `scripts/make_card_art.py` draws them as flat
illustrations (SVG sources in `assets/src`, 1200x675 JPEGs in `assets/cards`). Run
`uv run python scripts/make_card_art.py` to redraw them all, or add `--only <name>` for one; a
full run draws exactly the names below and removes any other art in those two folders.

To use richer art, such as images from an image model, generate each image below and save it as a
JPEG under the same name. Cards pick images by name (`yardsale/cards.py`), so no code changes.
Push to `main` (or set `YARDSALE_CARD_ART` to another https folder) for ASI:One to load them.
With `YARDSALE_CARD_ART` empty, cards show no images.

## Shared style

> Flat vector illustration, friendly and modern, soft gradient sky with faint stars, one rounded
> glass tile in the center holding the subject, two small glass bubbles with related objects at
> the sides, a few sparkles, no text, no letters or numbers, no logos or brand marks (a dollar
> sign on a coin is fine). 16:9, 1200x675. Each card has its own gradient: selling warm orange and
> coral, buying teal, other sites blue, money green, warnings amber, information indigo, and the
> menu a sunset.

## Cards (`assets/cards/<name>.jpg`)

| Name | Subject (side bubbles) |
| --- | --- |
| `menu` | A cheerful yard sale table under bunting: a lamp, a cardboard box, a stack of books, and a small bike beside it, each with a price tag (a price tag, a chat bubble); sunset |
| `sell` | A camera with a price tag hanging from it (a photo print, a coin); orange |
| `buy` | A shopping bag with a magnifying glass (a price tag with a down arrow, a star); teal |
| `pricing` | A magnifying glass over a small bar chart: agents researching prices (a camera, a price tag); orange |
| `proposal` | A clipboard with a price tag and a check mark (a photo print, a bar chart); coral |
| `live` | A friendly little robot agent with an antenna, holding a price tag (a chat bubble, a wireless signal); orange |
| `haggle` | Two speech bubbles facing each other, a coin between up and down arrows (a coin, a handshake); teal |
| `deal` | A handshake with sparkles (a stack of coins, a price tag); green |
| `pay` | A payment card with a padlock (a shield, a receipt); green |
| `paid` | A receipt with a big green check (a payment card with a padlock, a coin); green |
| `listings` | A little shop shelf under a striped awning, each item with a price tag (an open box, a chat bubble); coral |
| `orders` | A parcel box with a check mark (a shopping bag, a receipt); teal |
| `deals` | A globe with price tags pinned on it: deals on other sites (a magnifying glass, a price tag with a down arrow); blue |
| `watch` | A ringing bell with binoculars beside it (a clock with no numbers, a price tag with a down arrow); teal |
| `craigslist` | A pinned paper note with an envelope (a price tag, a chat bubble); blue |
| `ebay` | A shopping cart plugged into a socket: connecting a seller's own marketplace account, no logos (chain links, a price tag); blue |
| `info` | A friendly information badge (a chat bubble, a star); indigo |
| `warning` | A warning triangle with a shield (a price tag, an information badge); amber |
| `campus` | A campus hall with a clock tower and a backpack (a map pin, a chat bubble); indigo |
| `item` | A cardboard box with open flaps and a price tag, shown when an item has no photo (a price tag, a coin); tan |
