# eBay draft upload — Pokémon singles (29 cards)

`ebay-pokemon-drafts.csv` turns the 29 rows tagged `ebay` in the TCGplayer portfolio
export into eBay **drafts**, so you can upload now and add photos before publishing.

Regenerate any time with `python3 build_ebay_csv.py`.

## How to upload

1. Seller Hub → **Reports** → **Upload**.
2. Template type: **Create drafts** (not the full listing template — `Action=Draft`
   is rejected by the full template).
3. Upload `ebay-pokemon-drafts.csv`, then check the results report.
4. Seller Hub → **Listings** → **Drafts**, open each one, drop in your photos, and publish.

Drafts expire in roughly 30–45 days, so publish (or re-upload) before then.

## What's in each row

| Field | Value |
|---|---|
| Action | `Draft` |
| Category ID | `183454` — Toys & Hobbies › CCG › CCG Individual Cards |
| Condition ID | `4000` = Ungraded (`2750` would be Graded) |
| Condition descriptor | `Near mint or better - (ID: 400010)` — required for card listings since Oct 2023 |
| Format / Quantity | FixedPrice, GTC, qty 1 |
| Item photo URL | **blank on purpose** — you add photos in Seller Hub |
| SKU | `CJD-PKM-001` … `CJD-PKM-029`, in the same order as the export |

## Titles

All 29 titles are ≤ 80 characters (longest is 76) and front-load what buyers actually
search: `Pokemon` → card name → card number → set → rarity → finish → year → `NM`.
Card numbers are expanded to the full set count where the export only had a bare number
(`Machamp 8` → `8/102`, `Feraligatr 4` → `4/111`) since buyers search that way.

## Item specifics

Filled per card: Game, Card Name, Set, Card Number, Manufacturer, Rarity, Character,
Card Type, Finish, Features, Language, Graded (No), Autographed (No), Vintage,
Year Manufactured, Country/Region of Manufacture. Game/Set/Card Number/Rarity/Graded
are the ones eBay requires in this category.

## Pricing

Buy It Now = TCGplayer market price floored to the dollar plus `.99`
(e.g. `$67.43` → `$67.99`). Total listed value: **$2,702.71**. Edit the `Price`
column before uploading if you want different numbers, or add Best Offer per listing
in Seller Hub — the drafts template has no Best Offer column.

## Things to check before you publish

- **Shipping, returns and item location** aren't in the drafts template. They come from
  your Seller Hub defaults / business policies when you finish each draft. The
  descriptions promise tracked shipping, penny sleeve + top loader + team bag, same or
  next business day handling, and 30-day returns — make the listing settings match.
- **Set years** come from set release dates; `Ascended Heroes` (2026) was inferred from
  the export date since it's a current-year set.
- If your downloaded drafts template has slightly different headers than this file,
  paste the columns into eBay's copy rather than renaming theirs.
