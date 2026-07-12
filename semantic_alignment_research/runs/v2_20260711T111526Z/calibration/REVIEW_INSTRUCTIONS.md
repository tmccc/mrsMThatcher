# Semantic alignment review instructions

Review the quotation and image as a proposed editorial pairing. The fingerprints
and old critic are evidence, not an answer you must follow.

Judge the pairing as it would appear in the published post, with the quotation
shown alongside the image. Do not infer the generation prompt or intended design
brief.

## Relationship

Choose the primary way the image relates to the quotation. Add a secondary
relationship only when it captures a materially different connection, such as
an image which illustrates a claimed consequence while also substituting a
broader ideology.

## Relevance and directness

`relevance` asks whether the image materially connects to the quotation's
mechanism, claimed outcome or broader principle. `directness` asks how quickly a
reader can understand that connection without explanation.

Use `very_low`, `low`, `moderate`, `high` or `very_high`.

## Appropriateness

How well does this image fit this quotation?

1. Clearly inappropriate
2. Weak fit
3. Acceptable but indirect
4. Strong fit
5. Exceptional fit

## Publish likelihood

Would you actually choose to post this pairing? This can be lower than
appropriateness because an acceptable image may still be too generic, confusing,
unattractive or risky to publish.

1. Definitely would not publish
2. Unlikely to publish
3. Might publish
4. Likely to publish
5. Definitely would publish

## Image decision

If this were today's scheduled post, what would you do?

- `keep_current_image`: keep this image.
- `prefer_different_generated_image`: ask the selector to try another generated
  candidate. This does not guarantee the alternative will be better.
- `unsure`: defer the operational choice for later review.

Appropriateness asks how well the current image fits. Publish likelihood asks
whether you would post the pairing. Image decision asks whether you would keep
this image or ask the selector to try another generated candidate. A reviewer
can therefore give a moderate score but still keep the best available image, or
give a reasonably good score while preferring another candidate.

## Complete review

A review is complete only when it has a primary relationship, relevance band,
directness band, appropriateness rating, publish-likelihood rating and image
decision. Secondary relationship and notes are optional. Legacy reviews lacking
either rating or the decision remain incomplete until revised.

## Navigation

- `1`-`5`: appropriateness
- `Shift+1`-`Shift+5`: publish likelihood
- `K`: keep current image
- `D`: prefer a different generated image
- `U`: unsure
- `S`: save and next
- `N`: next without saving
- `P`: previous without saving

All controls remain available with mouse, touch and keyboard focus. Saving is
atomic and reopening a completed case permits revision without changing its case
ID.
