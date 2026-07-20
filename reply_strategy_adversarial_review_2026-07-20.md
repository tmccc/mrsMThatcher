# Reply strategy adversarial review

Date: 20 July 2026

Scope: `reply_strategy.py`, its production call path in `mrsMThatcher2.py`, and the directly relevant tests. This was a read-only code review apart from creating this report. No production code, state, service, network endpoint, or log was changed.

## Executive verdict

The module has extensive regression coverage and several sound fail-closed boundaries, but it still contains confirmed paths that can accept materially false or misleading replies. The most important weakness is architectural: selected evidence IDs and lexical overlap are treated as evidence that a reply is supported, without checking that the evidence entails the reply's actors, action, polarity, date, location, or relationship.

Eight substantive defects and two lower-severity hardening defects were reproduced. Existing tests all pass because they check expected positive and negative examples but do not exercise these adversarial substitutions.

## Confirmed findings

### 1. High: an unrelated evidence packet can "ground" an arbitrary false factual claim

Relevant code: `reply_strategy.py:1237-1273`, with production acceptance at `mrsMThatcher2.py:8540-8657`.

The validator proves that each selected ID was retrieved, attribution-eligible, and sufficiently confident. It never proves that the final factual assertion is entailed by the selected packet. No general topical or claim-to-evidence check runs for `historical_context`, `historical_correction`, or factual humour outside a recognised direct question.

Reproduction using the real completed corpus:

- incoming issue: Thatcher's approach to taxes and enterprise;
- retrieved packet: `a7f8b7c8dcea6f7ecddea2624f81c169e69f893bee4d5158d53ce35cdec1987a`, "The facts of life are Conservative.";
- accepted reply: "Margaret Thatcher nationalised every British railway in 1985."

The reply was accepted both by `validate_reply_decision()` and by the complete `ask_grok_for_reply()` path with only the HTTP response mocked. The later `generated_reply_is_safe_enough()` check also accepted it.

Impact: a model mistake, prompt injection, or incorrect evidence selection can be labelled grounded and posted even when the cited packet does not support the claim.

Recommended direction: add a conservative claim-to-evidence contract that checks named entities, dates, quantities, action, polarity and relation against selected packet fields. Where support cannot be demonstrated, reject to `no_reply`; do not rely on the model's own `grounded` flag.

### 2. High: unsupported actor allegations bypass the non-factual guard through unlisted verbs

Relevant code: `reply_strategy.py:53-90`, `reply_strategy.py:629-696`, and `reply_strategy.py:1284-1292`.

The local assertion guard is a predicate deny-list. Any new allegation verb outside that list can pass while `factual_claim_made=false`.

Confirmed accepted examples:

- `principle_reply`: "The government embezzled public funds."
- `wry_reply`: "Andy Burnham embezzled public funds."

The second sentence also passes the final production safety checker. Equivalent bypasses exist for verbs such as `falsified`, `rigged`, `bribed`, `defrauded`, and other predicates not enumerated by the regular expressions.

Impact: the model can repeat or invent a serious actor-specific allegation while declaring that it made no factual claim.

Recommended direction: replace verb enumeration with a positive structural policy. A non-factual mode should reject declarative claims about named people, handles, institutions, or concrete actors unless the construction is explicitly known to be non-assertive.

### 3. High: direct factual validation checks token presence, not the requested relation or truth value

Relevant code: `reply_strategy.py:903-1098`.

Several incorrect answers passed:

- both `Yes.` and `No.` passed the same evidence-backed yes/no question;
- "The United States." and "European Council." passed a `who` question because capitalised entities were treated as people;
- "Margaret Thatcher." passed "Who did she meet?" because the packet speaker was accepted as the requested counterpart;
- "People moved towards East Berlin." passed "Which side did people move towards when the Berlin Wall fell?";
- "People were prevented from moving from East Berlin to West Berlin." passed the `where` regression despite denying that the movement occurred;
- a sentence containing an early East-to-West clause and a later West-to-East correction passed because the first matching relation wins.

The Berlin-specific direction check applies only when the classifier returns `where`, not to the equivalent directional `which` question. The generic evidence check then accepts any overlapping entity or direction token.

Impact: a concise answer can be syntactically direct and use words present in a packet while answering with the wrong person, side, polarity, date, place, or causal relation.

Recommended direction: validate answer slots by question type. Yes/no needs evidence-backed polarity; `who` needs a source-grounded person in the requested semantic role; direction questions need one unambiguous, non-negated source-to-destination relation; dates and places need to be linked to the event asked about.

### 4. High: topical relevance can be established by one ambiguous token

Relevant code: `reply_strategy.py:306-319` and `reply_strategy.py:322-374`.

`_topic_pair_aligned()` returns true for any shared token not included in the finite generic-political vocabulary. This permits polysemy and incidental overlap.

Confirmed example:

- incoming: "Andy Burnham's employment record is disputed."
- `topical_basis`: "employment record"
- accepted reply: "The historical record must not be rewritten."

The two uses of `record` are semantically different, but the principle reply passes the relevance validator.

Impact: the earlier grounded-but-irrelevant reply defect can recur whenever the incoming post, evidence, and reply share an ambiguous word that is not on the generic-token list.

Recommended direction: do not treat one lexical token as a semantic bridge. Require a stronger relation between the complete issue proposition and reply proposition, with conservative abstention for ambiguous single-token matches.

### 5. Medium: fabricated quotations bypass verification through unsupported delimiters

Relevant code: `reply_strategy.py:1148-1159`.

The quotation checker recognises straight and common curly English quotation marks. It does not recognise several other visible quotation forms.

The fabricated text "Taxes vanished overnight" was correctly rejected in `"..."` and `“...”`, but accepted in:

- `«...»`
- `‹...›`
- `「...」`
- `„...“`
- backticks

The complete production safety checker also accepted the guillemet version.

Impact: invented wording can be visually presented as a quotation despite having no exact verified source.

Recommended direction: normalise or reject all Unicode quotation punctuation and common paired delimiters before exact-text verification.

### 6. Medium: common concrete questions are missed or classified as the wrong type

Relevant code: `reply_strategy.py:699-849`.

Confirmed classifications:

- `Did she resign?` -> not concrete
- `Did she win?` -> not concrete
- `Did she support sanctions?` -> not concrete
- `Was she prime minister?` -> not concrete
- `I'd like to know where the meeting happened?` -> not concrete
- `Can anyone tell me what happened?` -> not concrete
- `Do you happen to know who attended?` -> incorrectly classified as yes/no

The yes/no classifier depends on a finite factual-marker vocabulary or capitalised words. When it misses, the answer-first mode restriction and deterministic factual-answer validator do not run.

Impact: ordinary factual questions can still receive humour, a principle, or an answer to the wrong question type.

Recommended direction: parse common question preambles separately, then classify the embedded interrogative. Treat unambiguously factual auxiliary questions as concrete unless a specific subjective pattern excludes them.

### 7. Medium: retrieval can select evidence using fields that are not sent to the model

Relevant code: retrieval fields at `reply_strategy.py:137-141` and `reply_strategy.py:377-420`; prompt projection at `reply_strategy.py:188-203`.

Retrieval searches `historical_context`, `literal_meaning`, and `entities`, but `RetrievedEvidence.prompt_record()` omits all three. Therefore a packet can rank because of the exact fact requested while the model never receives that fact.

Confirmed real-corpus example:

- query: "What happened after the West Berlin discotheque bombing?"
- packet `36b2a8c...` was retrieved because `historical_context` contains `discotheque`;
- the generated prompt record did not contain `discotheque` or the full event context.

Impact: the model may abstain unnecessarily or infer an answer from incomplete evidence even though the local corpus contains the required fact.

Recommended direction: make the prompt projection and retrieval index use the same approved evidence fields, subject to a bounded prompt size.

### 8. Medium: valid factual humour drafts cannot survive a retryable posting failure

Relevant integration code: `mrsMThatcher2.py:8121-8150` and `mrsMThatcher2.py:8187-8208`.

`reply_strategy.py` explicitly permits factual humour with grounded packet IDs. The pending-draft writer only retains incoming context for historical and principle modes, and the reader only re-retrieves evidence for historical modes.

Confirmed sequence:

1. a factual `wry_reply` with evidence was accepted and stored;
2. its `incoming_text` was stored as `null`;
3. immediate `pending_strategy_reply()` revalidation discarded it because its evidence ID was no longer in the empty retrieved set.

Impact: after an ambiguous or retryable X write failure, this supported mode buys another model decision instead of reusing the durable draft. This weakens the intended transactional behaviour and can change reply wording between attempts.

Recommended direction: either prohibit factual humour modes or persist and re-retrieve context for every grounded draft, based on metadata rather than mode name.

### 9. Low: mode, tone, confidence and evidence metadata can contradict one another

Relevant code: `reply_strategy.py:1207-1273`.

Confirmed accepted combinations include:

- `wry_reply` with `humour_tone=warm`;
- non-factual humour carrying high-confidence grounded evidence;
- `evidence_confidence=high` backed only by a medium-confidence packet.

Impact: strategy telemetry and digest aggregates can misdescribe what the model produced and overstate source confidence.

Recommended direction: define a mode-to-tone mapping and make declared confidence no higher than the weakest material packet. Define whether non-factual grounded humour is supported, then validate it consistently.

### 10. Low: sentence and emoji limits are bypassable

Relevant code: `reply_strategy.py:1136-1145`.

Confirmed examples:

- `Quite so.Another thought.Third point.Fourth remark.` is counted as one sentence because only punctuation followed by whitespace or end-of-string is counted;
- keycap, copyright, trade-mark and arrow emoji sequences using variation selectors are not detected.

Impact: replies can violate the stated two-sentence/no-emoji policy, although the 270-character limit still bounds size.

Recommended direction: tokenise sentence boundaries independently of following whitespace and detect emoji presentation sequences, variation selectors and keycap composition.

## Existing protections that held

- all 211 `tests/test_reply_strategy.py` tests passed;
- seven directly relevant pending/durable-metadata tests passed;
- bare `SKIP` remains rejected;
- `no_reply` conditional schema and local normalisation remain fail-closed;
- attribution-ineligible and unresolved packet IDs remain rejected;
- historical modes still require selected evidence and configured confidence;
- straight and common curly invented quotations are rejected;
- retrieval remains deterministic and network-free;
- `ruff check reply_strategy.py` passed;
- Bandit's only finding was a false SQL-injection match against prompt prose;
- Pylint reported complexity and line-length findings, but no additional functional defect. The complexity warnings are material because the two principal validators have 40-plus branches each.

## Commands and probes

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_reply_strategy.py` -> 211 passed
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_unit_helpers.py -k 'strategy_metadata or pending_strategy or pending_reply_is_valid_direct_answer'` -> 7 passed, 400 deselected
- `ruff check reply_strategy.py` -> passed
- `pylint --reports=no --score=no reply_strategy.py` -> complexity/style findings only
- `bandit -q reply_strategy.py` -> one confirmed false positive in a prompt string
- network-free direct validator probes for claims, quotations, relevance, question classification, relation direction, metadata and retry persistence
- one network-free production-path probe with `requests.post` mocked locally

No full test suite was run because no code was changed and the focused 218 tests cover the reviewed surfaces. The adversarial probes intentionally assert behaviours absent from the current suite.

## Production isolation

Before and after review:

- service state: active/running;
- wrapper PID: `1595442`;
- Python child PID: `1595443`;
- start time: `Sun 2026-07-19 23:07:45 BST`;
- restart count: `0`.

No X post, provider call, service restart, production-state write, code change, commit, push, or deployment occurred.

## Recommended repair order

1. Require evidence entailment for factual and historical modes.
2. Replace the ungrounded allegation verb deny-list with a structural fail-closed rule.
3. Make direct factual validation relation-, role- and polarity-aware.
4. Strengthen topical relevance beyond one-token overlap.
5. Cover all visible quotation delimiters.
6. Broaden and correctly unwrap concrete-question forms.
7. Align prompt evidence fields with retrieval fields.
8. Make grounded factual humour retry-safe or disallow it.
9. Tighten metadata consistency.
10. Harden sentence and emoji detection.
