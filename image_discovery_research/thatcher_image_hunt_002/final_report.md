# Thatcher Image Hunt 002

- Existing baseline: 69 images (schema v3)
- Gemini model: `gemini-3.1-pro-preview`
- Attempted logical calls: 12
- Provider-completed logical calls: 12
- Developer attempts: 13
- Vertex attempts: 0
- Developer-to-Vertex failover: no
- Grounded source pages harvested: 400
- Model source records rejected for absent provider grounding: 0
- Images downloaded: 132
- Reviewable triaged candidates: 85
- Approximate review target: 140 (not reached)
- Possible higher-quality replacements: 0
- Known spend by transport: `{"developer_api": 1.4485796, "vertex": 0.0}`

- Responses lost before the parser checkpoint defect was fixed: 0 (estimated spend US$0.00; conservative possible exposure up to US$0.00)

## Coverage priorities

- early career, Finchley campaigning and Education Secretary years
- election canvassing, crowds, rallies and active outdoor campaigning
- factories, shops, farms, schools and industrial visits
- conversations with workers and members of the public
- Cabinet, parliamentary, desk-work and behind-the-scenes working scenes
- international diplomacy with source-identified Reagan, Gorbachev and Cold War context
- Falklands, defence, European Council and Bruges-era settings
- warm, humorous, laughing, informal and later-life book-event scenes
- group photographs and high visual-energy compositions

## Duplicate classes

- alternate_scan: 2
- exact_duplicate: 5
- near_duplicate: 40
- visually_distinct: 85

## Rights status

- attribution_required: 16
- editorial_or_licensed_only: 2
- public_domain: 49
- rights_unclear: 18

## Archive harvesting

- Commons API requests: 133
- Commons API 429 events: 11
- Commons image-download 429 failures: 617
- Bounded image rate-limit cycles exhausted: 2
- Further automatic archive recovery disabled: yes

## Automated triage priority

- high: 25
- low: 16
- medium: 31
- reject: 13

## Stop condition

Target not reached: 85 reviewable candidates were obtained, 55 below the approximate target.
The run stopped at the 12-logical-call ceiling after two bounded Commons image-rate-limit recovery cycles. No weak, unattributed or untriaged candidates were added as padding.

## Review

- Human review complete: yes
- Reviewed: 85 of 85
- Keep: 26
- Maybe: 2
- Reject: 57
- Kept candidates with source-evidenced production-ready rights: 22
- Kept rights statuses: `{"attribution_required": 3, "public_domain": 19, "rights_unclear": 4}`

The pilot gate passed and all 85 locally stored candidates have been reviewed. To revise a decision, run:

```bash
python3 thatcher_image_hunt.py serve-review --research-dir image_discovery_research/thatcher_image_hunt_002 --host 127.0.0.1 --port 8768
```

Export only kept candidates to the isolated research export directory:

```bash
python3 thatcher_image_hunt.py export-kept --research-dir image_discovery_research/thatcher_image_hunt_002 --output-dir image_discovery_research/thatcher_image_hunt_002/exported_kept
```

No candidate was added to the production image collection. Human review is complete; an explicit isolated export remains required.
