# Comp cert sourcing notes

Goal: for each graded card, find cert numbers of *other* slabs of the same card at
the same grade, to use as comp anchors.

## Status

| Category | Cards | Same-grade certs found |
|---|---|---|
| PSA | 8 | 6 |
| CGC | 35 | 0 |
| Beckett | 10 | 0 |

## The method that works: cert-in-URL retailers

Some graded-card retailers encode the cert number in the product URL slug, next to
the year, set, card number, and grade:

```
gamestop.com/.../giovannis-charisma-special-illustration-rare-psa-10/PSA89984568M.html
aamintcards.com/1999-pokemon-base-set-hitmonchan-7-holo-cgc-10-gem-mint-1401039554006/
pokemonsteve.com/.../espeon-ex-terastal-fest-ex-211-187-...-cgc-10-pristine-6030681023
```

This is the only reliable remote source found. The slug asserts both which card it is
and which cert belongs to it, in one indexed string — so a match can be trusted
without opening the page.

Known cert-in-slug retailers:

| Site | Graders carried |
|---|---|
| gamestop.com | PSA only |
| aamintcards.com | PSA, CGC, SGC |
| pokemonsteve.com | CGC |

No cert-in-slug retailer was found for Beckett/BGS.

Cert formats, for sanity-checking any number you collect:
- **PSA** — 8–9 digits
- **CGC** — 13-digit legacy (`1401039554006`) or 10-digit current (`6103173088`)
- **BGS** — on the slab back, not consistently published anywhere online

## Why eBay does not work for this

eBay item URLs use eBay's own item ID, never the cert:
`ebay.com/itm/187855796855`. Titles carry card and grade but not the cert — that
lives in the listing body and the slab photo, neither of which is indexed or
fetchable here.

**Trap:** eBay item IDs are 12 digits and CGC legacy certs are 13. They look nearly
identical. An eBay ID pasted into CGC's lookup will either miss or resolve to an
unrelated card. Beckett catalog IDs are the same hazard —
`beckett.com/pokemon/2021/pokemon-celebrations/005-pikachu-holo-r-20494767` ends in a
*spec* ID, not a cert.

## Trap: search summaries invent cert numbers

Search-engine prose summaries fabricated certs that appear in no source. One claimed a
CGC cert of `135870793` for the McDonald's Pikachu — 9 digits, matching no CGC format
(it is PSA-shaped). Another asserted `1401043438026` for the Brilliant Stars Eevee
with no URL backing it.

Only certs read directly out of a URL slug are recorded in the CSVs. Anything that
appeared only in prose was discarded.

## IMPORTANT: the PSA certs are unverified

None were confirmed against psacard.com. This session's egress policy blocks the
verification hosts — `psacard.com`, `cgccards.com`, `beckett.com`, `ebay.com` and
effectively all non-allowlisted domains return `403` on `CONNECT` at the proxy.
Verify each at `psacard.com/cert/<number>` before relying on it.

## Two PSA cards had no same-grade match

- **Monkey.D.Luffy (118) Parallel, OP10-118, PSA 9** — only PSA 10 listings exist.
  PSA 10 certs for the same card: 107146927, 158938458
- **Minccino (JP) 082/071 Wild Force, PSA 9** — only PSA 10 listings exist.
  PSA 10 cert for the same card: 91002713

## Why CGC and Beckett came back empty

The method is sound but inventory-bound: it only produces a cert when one of the three
retailers happens to have that exact card at that exact grade in indexed inventory.
Searches across the highest-value CGC cards (Shining Synergy Pikachu CN, Marnie's
Morpeko, Terastal Espeon ex, McDonald's Pikachu, Snorlax 051, Lost Origin Charizard,
Celebrations Blastoise) returned listings on eBay and Fanatics Collect — neither of
which exposes certs — and no slug match at the right grade.

Closest near-miss: pokemonsteve.com has Espeon ex 211/187 at **CGC 10 Pristine**
(cert 6030681023). The copy here is CGC 8.5, so it is not a comp.

Beckett is structurally harder: no retailer publishes BGS certs in URLs, and Beckett's
own lookup is cert-in/details-out like the others.

## Targeted attempt: first 5 CGC cards (all failed)

| # | Card | Grade | Result |
|---|---|---|---|
| 1 | Pikachu V (Full Art) 157/172, Brilliant Stars | CGC 8.5 | no slug source |
| 2 | Eevee TG11/TG30, Brilliant Stars TG | CGC 10 Gem Mint | no slug source |
| 3 | Blastoise 2/102, Celebrations Classic | CGC 9.0 | AA Mint has this card in PSA 9/10 only |
| 4 | Dark Gyarados 8/82, Celebrations Classic | CGC 9.0 | no slug source |
| 5 | Tyranitar (JP) 079/071, Clay Burst | CGC 10 Gem Mint | listings exist, none expose cert |

Every one of these has CGC copies for sale at the right grade — on eBay, PKMhobby,
Monmouth Sports Cards, Graded Power, Fanatics Collect. None of those sites put the
cert in the URL, so the number is unreachable without opening the page or reading the
slab photo, and both are blocked here.

Additional retailers checked, none cert-in-slug: `pkmhobby.com`, `monmouthcards.com`,
`gradedpower.com` (its URL digits are internal catalog IDs, not certs),
`fanaticscollect.com` (UUIDs), `collectibles.com`, `sweetsandgeeks.com`.

CGC's own site is also a dead end: `certlookup` pages are indexed only as the generic
landing page, never associated with card names, and population-report pages give
counts per grade rather than cert numbers.

## To finish the remaining 45

Either run the same searches from a network that can reach the blocked hosts, or search
the three cert-in-slug retailers directly by card name and grade. For Beckett
specifically, the cert most likely has to come off the slab photo in a listing, read by
eye — there is no indexed source.
