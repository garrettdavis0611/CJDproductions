# Comp cert sourcing notes

Goal: for each graded card, find cert numbers of *other* slabs of the same card at
the same grade, to use as comp anchors.

## Status

| Category | Cards | Same-grade certs found |
|---|---|---|
| PSA | 8 | 6 |
| CGC | 35 | 0 |
| Beckett | 10 | 0 |

## Where the PSA certs came from

GameStop's graded-card catalog encodes the cert number in the product URL slug,
alongside year, set, card number, and grade:

```
.../2023-pokemon-mew-en-151-204-giovannis-charisma-special-illustration-rare-psa-10/PSA89984568M.html
     ^-- year/set/number/name/grade                                                    ^-- cert 89984568
```

The trailing `M` is a GameStop SKU marker, not part of the cert.

Because the slug states set + card number + grade independently of the cert digits,
a match is high-confidence: the same string asserts both which card it is and which
cert belongs to it.

## IMPORTANT: these are unverified

None of these certs were confirmed against psacard.com. This session's egress policy
blocks the verification hosts — `psacard.com`, `cgccards.com`, `ebay.com` and
effectively all non-allowlisted domains return `403` on `CONNECT` at the proxy.
Verify each cert at `psacard.com/cert/<number>` before relying on it.

## Two PSA cards had no same-grade match

- **Monkey.D.Luffy (118) Parallel, OP10-118, PSA 9** — only PSA 10 listings exist.
  PSA 10 certs for the same card: 107146927, 158938458
- **Minccino (JP) 082/071 Wild Force, PSA 9** — only PSA 10 listings exist.
  PSA 10 cert for the same card: 91002713

## Why CGC and Beckett returned nothing

GameStop's graded program is PSA-only, so the slug trick does not extend to them.
No equivalent cert-in-URL retailer was found for CGC or Beckett. The venues that do
list CGC/Beckett certs (eBay listing bodies, CGC's own lookup, Fanatics Collect)
are all blocked by this session's egress policy, and cert numbers do not appear in
search-result snippets — they live in page bodies and slab photos.

Unblocking either requires an environment whose network policy permits those hosts.
