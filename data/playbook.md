# Contracting playbook (synthetic house rules)

## Board codes
RO room only, BB bed and breakfast, HB half board, FB full board, AI all inclusive. "Half board" or "dinner included" is HB. A board supplement ("AI + 25") means the full price is the base board price plus the supplement.

## Default commercial terms
Our default is net rates, per room per night, in the contract currency. If a hotel quotes per person or commissionable rates, record what they quote and flag it for review: the offer rows convert per person to per room.

## Offer changes inherit the existing offer
When a hotel extends or changes an existing special offer ("extend the early booking to end of Feb", "same conditions"), only the fields they mention change. Every other field (discount, stay window, boards, combinability) is copied from the offer in the existing contract.

## Stop sales and re-opens
A stop sale closes sales for the stated room types and stay dates. If no room type is named, it applies to all rooms. "Fully booked", "please close" and a hotel closure are stop sales. "Open again" or "you can sell again" is an open_sale for the same rooms and dates as the earlier stop sale.

## Room names to our codes
Map the hotel's room wording to the room codes in that hotel's existing contract. If a name could match two rooms (for example "sea view" when there is a Double Sea View and a Sea View Suite), flag the field for review instead of guessing.

## Dates without a year
Infer the year from the contract period of the existing contract. A date that falls outside the contract period must be flagged.

## Release and allotment
Release days and allotment counts only apply to allotment rooms. A change of release on a free sale hotel is suspicious: flag it.

## Rate changes
A rate change states the room, board, dates and either the new price or a delta ("+6 EUR", "-10%"). Compute the new price from the existing contract rate and mark the value as derived. If the existing rate can't be found, flag the field.

## Hotel identity
Match the hotel by the sender's email domain first, then by name, then by contact person. Names can be close ("Hotel Sol de Marina" and "Hotel Marina Sol" are different hotels). If the match is weak, flag it: getting the hotel wrong corrupts every field.
