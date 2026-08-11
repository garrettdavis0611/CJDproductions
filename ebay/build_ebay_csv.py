#!/usr/bin/env python3
"""Build an eBay "Create drafts" bulk-upload CSV from the TCGplayer portfolio export.

Source: the rows whose Portfolio Name is "ebay" in the export CSV.
Output: ebay-pokemon-drafts.csv  (eBay Seller Hub > Reports > Upload, Create drafts template)

Photos are intentionally left blank so the drafts can be opened in Seller Hub
and finished with real photos before publishing.
"""

import csv
import math
import os

CATEGORY_ID = "183454"          # Toys & Hobbies > CCG > CCG Individual Cards
CONDITION_ID = "4000"           # Ungraded (2750 = Graded)
CD_HEADER = "CD:Card Condition - (ID: 40001)"
CD_NM = "Near mint or better - (ID: 400010)"

ACTION_HEADER = "*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"

TPC = "The Pokemon Company"
WOTC = "Wizards of the Coast"

# sku, title, card name, set, number, rarity, finish, features, character,
# year, manufacturer, vintage, market price
CARDS = [
    ("CJD-PKM-001",
     "Pokemon Psyduck 226/217 Ascended Heroes Illustration Rare Holo Card NM",
     "Psyduck", "Ascended Heroes", "226/217", "Illustration Rare", "Holo",
     "Illustration Rare, Holo, Secret Rare", "Psyduck", "2026", TPC, "No", 67.43),

    ("CJD-PKM-002",
     "Pokemon Garchomp V TG23/TG30 Astral Radiance Trainer Gallery Ultra Rare NM",
     "Garchomp V", "Astral Radiance Trainer Gallery", "TG23/TG30", "Ultra Rare", "Holo",
     "Trainer Gallery, Full Art, Holo", "Garchomp", "2022", TPC, "No", 84.76),

    ("CJD-PKM-003",
     "Pokemon Machamp 8/102 Base Set Shadowless 1st Edition Holo Rare 1999 NM",
     "Machamp", "Base Set (Shadowless)", "008/102", "Holo Rare", "Holo",
     "1st Edition, Shadowless, Holo, Vintage", "Machamp", "1999", WOTC, "Yes", 80.52),

    ("CJD-PKM-004",
     "Pokemon Misty's Psyduck 193/182 Destined Rivals Illustration Rare Holo NM",
     "Misty's Psyduck", "Destined Rivals", "193/182", "Illustration Rare", "Holo",
     "Illustration Rare, Holo, Secret Rare", "Psyduck", "2025", TPC, "No", 60.63),

    ("CJD-PKM-005",
     "Pokemon Diglett #50 EX Crystal Guardians Reverse Holo Common 2006 NM",
     "Diglett", "EX Crystal Guardians", "50", "Common", "Reverse Holo",
     "Reverse Holo", "Diglett", "2006", TPC, "No", 40.08),

    ("CJD-PKM-006",
     "Pokemon Gengar VMAX 157/264 Fusion Strike Ultra Rare Holo Card NM",
     "Gengar VMAX", "Fusion Strike", "157/264", "Ultra Rare", "Holo",
     "Full Art, Holo", "Gengar", "2021", TPC, "No", 45.82),

    ("CJD-PKM-007",
     "Pokemon Giovanni's Machamp 6/132 Gym Challenge Holo Rare Unlimited 2000 NM",
     "Giovanni's Machamp", "Gym Challenge", "6/132", "Holo Rare", "Holo",
     "Unlimited, Holo, Vintage", "Machamp", "2000", WOTC, "Yes", 80.66),

    ("CJD-PKM-008",
     "Pokemon Exeggcute 75/110 Legendary Collection Reverse Holo Common 2002 NM",
     "Exeggcute", "Legendary Collection", "75/110", "Common", "Reverse Holo",
     "Reverse Holo, Vintage", "Exeggcute", "2002", WOTC, "Yes", 92.50),

    ("CJD-PKM-009",
     "Pokemon Kadabra 49/110 Legendary Collection Reverse Holo Uncommon 2002 NM",
     "Kadabra", "Legendary Collection", "49/110", "Uncommon", "Reverse Holo",
     "Reverse Holo, Vintage", "Kadabra", "2002", WOTC, "Yes", 169.24),

    ("CJD-PKM-010",
     "Pokemon Omastar 58/110 Legendary Collection Reverse Holo Uncommon 2002 NM",
     "Omastar", "Legendary Collection", "58/110", "Uncommon", "Reverse Holo",
     "Reverse Holo, Vintage", "Omastar", "2002", WOTC, "Yes", 114.70),

    ("CJD-PKM-011",
     "Pokemon Primeape 59/110 Legendary Collection Reverse Holo Uncommon 2002 NM",
     "Primeape", "Legendary Collection", "59/110", "Uncommon", "Reverse Holo",
     "Reverse Holo, Vintage", "Primeape", "2002", WOTC, "Yes", 72.72),

    ("CJD-PKM-012",
     "Pokemon Sandslash 62/110 Legendary Collection Reverse Holo Uncommon 2002 NM",
     "Sandslash", "Legendary Collection", "62/110", "Uncommon", "Reverse Holo",
     "Reverse Holo, Vintage", "Sandslash", "2002", WOTC, "Yes", 101.99),

    ("CJD-PKM-013",
     "Pokemon Tentacruel 66/110 Legendary Collection Reverse Holo Uncommon 2002 NM",
     "Tentacruel", "Legendary Collection", "66/110", "Uncommon", "Reverse Holo",
     "Reverse Holo, Vintage", "Tentacruel", "2002", WOTC, "Yes", 90.64),

    ("CJD-PKM-014",
     "Pokemon Gengar TG06/TG30 Lost Origin Trainer Gallery Ultra Rare Holo NM",
     "Gengar", "Lost Origin Trainer Gallery", "TG06/TG30", "Ultra Rare", "Holo",
     "Trainer Gallery, Holo", "Gengar", "2022", TPC, "No", 54.89),

    ("CJD-PKM-015",
     "Pokemon Pikachu TG05/TG30 Lost Origin Trainer Gallery Ultra Rare Holo NM",
     "Pikachu", "Lost Origin Trainer Gallery", "TG05/TG30", "Ultra Rare", "Holo",
     "Trainer Gallery, Holo", "Pikachu", "2022", TPC, "No", 49.84),

    ("CJD-PKM-016",
     "Pokemon Mega Latias ex 181/132 Mega Evolution Special Illustration Rare NM",
     "Mega Latias ex", "Mega Evolution", "181/132", "Special Illustration Rare", "Holo",
     "Special Illustration Rare, Holo, Secret Rare", "Latias", "2025", TPC, "No", 86.73),

    ("CJD-PKM-017",
     "Pokemon Haunter 027 Mega Evolution Promo Illustration Rare Holo Card NM",
     "Haunter", "Mega Evolution Promos", "027", "Illustration Rare", "Holo",
     "Promo, Illustration Rare, Holo", "Haunter", "2025", TPC, "No", 51.20),

    ("CJD-PKM-018",
     "Pokemon Pikachu 58/102 E3 Stamped Promo 2019 Non-Holo Card NM",
     "Pikachu (E3 Stamped)", "Miscellaneous Promo", "058/102", "Promo", "Regular",
     "Promo, Stamped", "Pikachu", "2019", TPC, "No", 49.45),

    ("CJD-PKM-019",
     "Pokemon Feraligatr 4/111 Neo Genesis 1st Edition Holo Rare 2000 NM",
     "Feraligatr", "Neo Genesis", "4/111", "Holo Rare", "Holo",
     "1st Edition, Holo, Vintage", "Feraligatr", "2000", WOTC, "Yes", 200.00),

    ("CJD-PKM-020",
     "Pokemon Shining Rayquaza 56/73 Shining Legends Shiny Holo Rare 2017 NM",
     "Shining Rayquaza", "Shining Legends", "56/73", "Shiny Holo Rare", "Holo",
     "Shiny, Holo, Secret Rare", "Rayquaza", "2017", TPC, "No", 148.48),

    ("CJD-PKM-021",
     "Pokemon Rayquaza VMAX TG20/TG30 Silver Tempest Trainer Gallery Ultra Rare NM",
     "Rayquaza VMAX", "Silver Tempest Trainer Gallery", "TG20/TG30", "Ultra Rare", "Holo",
     "Trainer Gallery, Holo", "Rayquaza", "2022", TPC, "No", 204.05),

    ("CJD-PKM-022",
     "Pokemon Eevee & Snorlax GX SM169 Sun Moon Black Star Promo Ultra Rare NM",
     "Eevee & Snorlax GX", "Sun & Moon Black Star Promos", "SM169", "Promo", "Holo",
     "Promo, Tag Team, Full Art, Holo", "Eevee, Snorlax", "2019", TPC, "No", 183.45),

    ("CJD-PKM-023",
     "Pokemon Pikachu 173/165 SV 151 Illustration Rare Holo Card NM",
     "Pikachu", "Scarlet & Violet 151", "173/165", "Illustration Rare", "Holo",
     "Illustration Rare, Holo, Secret Rare", "Pikachu", "2023", TPC, "No", 92.08),

    ("CJD-PKM-024",
     "Pokemon Squirtle 170/165 SV 151 Illustration Rare Holo Card NM",
     "Squirtle", "Scarlet & Violet 151", "170/165", "Illustration Rare", "Holo",
     "Illustration Rare, Holo, Secret Rare", "Squirtle", "2023", TPC, "No", 88.70),

    ("CJD-PKM-025",
     "Pokemon Dark Alakazam 18/82 Team Rocket 1st Edition Rare 2000 Card NM",
     "Dark Alakazam", "Team Rocket", "18/82", "Rare", "Regular",
     "1st Edition, Non-Holo, Vintage", "Alakazam", "2000", WOTC, "Yes", 32.27),

    ("CJD-PKM-026",
     "Pokemon Pikachu & Zekrom GX 33/181 Team Up Ultra Rare Holo 2019 NM",
     "Pikachu & Zekrom GX", "Team Up", "33/181", "Ultra Rare", "Holo",
     "Tag Team, Holo", "Pikachu, Zekrom", "2019", TPC, "No", 59.61),

    ("CJD-PKM-027",
     "Pokemon Oshawott 105/086 White Flare Illustration Rare Holo Card NM",
     "Oshawott", "White Flare", "105/086", "Illustration Rare", "Holo",
     "Illustration Rare, Holo, Secret Rare", "Oshawott", "2025", TPC, "No", 94.07),

    ("CJD-PKM-028",
     "Pokemon Psyduck 20/53 WOTC Black Star Promo Vintage Card NM",
     "Psyduck", "Wizards Black Star Promos", "20/53", "Promo", "Regular",
     "Promo, Vintage", "Psyduck", "2000", WOTC, "Yes", 77.18),

    ("CJD-PKM-029",
     "Pokemon M Garchomp EX XY168 XY Black Star Promo Ultra Rare Holo NM",
     "M Garchomp EX", "XY Black Star Promos", "XY168", "Promo", "Holo",
     "Promo, Full Art, Holo", "Garchomp", "2016", TPC, "No", 114.38),
]


def price_for(market):
    """Charm-price just above TCGplayer market: 67.43 -> 67.99, 200.00 -> 200.99."""
    return f"{math.floor(market) + 0.99:.2f}"


def description(title, name, set_name, number, rarity, finish, features, year, market):
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'line-height:1.6;color:#222;max-width:800px;">'
        f"<h2 style=\"margin:0 0 8px;\">{name} &ndash; {set_name} {number}</h2>"
        f"<p>Authentic English Pok&eacute;mon TCG single in <b>Near Mint</b> condition, "
        f"pulled and stored in a smoke-free home. The photos show the exact card you "
        f"will receive &mdash; no stock images.</p>"
        "<h3 style=\"margin:16px 0 6px;\">Card Details</h3>"
        "<ul style=\"margin:0;padding-left:20px;\">"
        f"<li><b>Card:</b> {name}</li>"
        f"<li><b>Set:</b> {set_name}</li>"
        f"<li><b>Card Number:</b> {number}</li>"
        f"<li><b>Rarity:</b> {rarity}</li>"
        f"<li><b>Finish:</b> {finish}</li>"
        f"<li><b>Features:</b> {features}</li>"
        f"<li><b>Year:</b> {year}</li>"
        "<li><b>Language:</b> English</li>"
        "<li><b>Grading:</b> Ungraded / raw</li>"
        "</ul>"
        "<h3 style=\"margin:16px 0 6px;\">Condition</h3>"
        "<p>Graded by eye as <b>Near Mint</b>: sharp corners, clean edges, a glossy "
        "surface and solid centering, with no creases, bends, dents or writing. Any "
        "print lines or minor factory flaws are shown in the photos. This card has not "
        "been professionally graded, so please review every photo and judge condition "
        "for yourself before buying.</p>"
        "<h3 style=\"margin:16px 0 6px;\">Shipping &amp; Packaging</h3>"
        "<p>Shipped in a penny sleeve, then a rigid top loader sealed in a team bag, "
        "packed between cardboard in a bubble mailer. Tracked shipping on every order, "
        "with same or next business day handling.</p>"
        "<h3 style=\"margin:16px 0 6px;\">Returns</h3>"
        "<p>30 day returns accepted. Message me with any question about condition, "
        "centering or extra photos and I will get back to you quickly. Combined "
        "shipping is available on multiple cards.</p>"
        "</div>"
    )


def main():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ebay-pokemon-drafts.csv")

    headers = [
        ACTION_HEADER,
        "Custom label (SKU)",
        "Category ID",
        "Title",
        "UPC",
        "Price",
        "Quantity",
        "Item photo URL",
        "Condition ID",
        CD_HEADER,
        "Description",
        "Format",
        "C:Game",
        "C:Card Name",
        "C:Set",
        "C:Card Number",
        "C:Manufacturer",
        "C:Rarity",
        "C:Character",
        "C:Card Type",
        "C:Finish",
        "C:Features",
        "C:Language",
        "C:Graded",
        "C:Autographed",
        "C:Vintage",
        "C:Year Manufactured",
        "C:Country/Region of Manufacture",
    ]

    rows = []
    for (sku, title, name, set_name, number, rarity, finish, features,
         character, year, maker, vintage, market) in CARDS:
        assert len(title) <= 80, f"title too long ({len(title)}): {title}"
        rows.append({
            ACTION_HEADER: "Draft",
            "Custom label (SKU)": sku,
            "Category ID": CATEGORY_ID,
            "Title": title,
            "UPC": "Does not apply",
            "Price": price_for(market),
            "Quantity": "1",
            "Item photo URL": "",
            "Condition ID": CONDITION_ID,
            CD_HEADER: CD_NM,
            "Description": description(title, name, set_name, number, rarity,
                                       finish, features, year, market),
            "Format": "FixedPrice",
            "C:Game": "Pokemon TCG",
            "C:Card Name": name,
            "C:Set": set_name,
            "C:Card Number": number,
            "C:Manufacturer": maker,
            "C:Rarity": rarity,
            "C:Character": character,
            "C:Card Type": "Pokemon",
            "C:Finish": finish,
            "C:Features": features,
            "C:Language": "English",
            "C:Graded": "No",
            "C:Autographed": "No",
            "C:Vintage": vintage,
            "C:Year Manufactured": year,
            "C:Country/Region of Manufacture": "United States",
        })

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(float(r["Price"]) for r in rows)
    print(f"wrote {len(rows)} drafts to {out_path}")
    print(f"listed value: ${total:,.2f}")
    print(f"longest title: {max(len(r['Title']) for r in rows)} chars")


if __name__ == "__main__":
    main()
