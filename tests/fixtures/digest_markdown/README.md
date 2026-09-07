# Markdown compatibility fixtures

These fixed JSON reports and exact UTF-8 Markdown outputs were generated with
the unmodified `mrs_log_digest.py` at
`e08d894d9cd39a07ebe4eeb72205baa864d1a2be`, before the renderer extraction.
Expected output is compared byte for byte, including whitespace and the final
newline. No fields or text are normalised during comparison.

- `populated`: reuses prepared data from the existing generated-image,
  carried-state, single-call reply and published-cost tests, with a small receipt
  lifecycle and event example. Covers stale state, costs, detailed image output,
  event ordering, Unicode, pipe/newline escaping and long-cell truncation.
- `missing`: only the required summary mapping is present.
- `optional`: explicit null/empty sections, unavailable cost data, and carried
  state with no timestamp or optional trial fields.

Paths and time values are synthetic and fixed; loading these reports performs
no state reads or provider calls. Keep expected Markdown independent of the
current renderer when making deliberate presentation changes.

The populated historical-reply table was deliberately updated for the combined
bot/digest review fixes to include public post identity and confirmed-text
status/source columns. Its legacy synthetic row leaves these unavailable fields
empty; the source JSON remains unchanged.
