# Black Rhino Catalogue Import Review

Source: the three photographed stock-taking pages supplied in the conversation.

## Import rules applied

- The handwritten/printed row numbers are **not** used as product ordering keys.
- Catalogue order is grouped by category, then product name, then size (1L, 750ml, 500ml, 350ml, 250ml, then unspecified packaging).
- `1/2` and `HALF` are normalized to **500ml**.
- `1/4` is normalized to **250ml**.
- Where the source gives no stock quantity, opening stock is **0**.
- Where the source gives no cost price, cost price is **0** (unknown, not an assumed cost).
- Where the source gives no wholesale price, wholesale price is **0** (unknown).
- Selling prices are loaded exactly as transcribed where readable.

## Items requiring confirmation

The source images do not show a readable selling price for:

1. Double Black 1ltr
2. Double Black 750ml

Those variants are imported with selling price `0.00` so the POS will not silently invent a price. Confirm the prices in the Products screen before selling them.

## Source spellings retained where uncertain

The following source spellings were retained rather than silently corrected because the photographs do not provide enough evidence to make a reliable correction:

- Henken
- Goddons / Goddons Can Pink
- Fax Can
- Casaa Bwena
- Jimmy Jimro
- Jagamiser
- Crome
- Dresso Mobile
- Sportman
- K.O

They can be edited later by an authorized manager/admin.
