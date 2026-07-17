# Discovered Image Integration Preparation

- Production-ready: 24 (22 original keeps + 2 promoted maybes)
- Rights pending: 4
- Maybe second pass: 2
- Canonical analyses: 24 with `grok-4.3` / `source-grounded-image-analysis-2026-07-16-v1`
- Analysis source: `source_grounded_image_analysis.json`
- Combined known analysis cost: US$0.2821
- Ambiguous possible exposure: US$0.1500
- Planned filenames: `t71.jpg` through `t94.jpg` (not applied)
- Material coverage improvements: 14
- Never competitive: 8
- Broad-dominance flags: 1
- Suspicious pairing flags: 0
- Pairing review: 101/101 complete
- Approved pairings: 17
- Preferred existing image: 83
- Maybe images promoted to keep: 2
- Automated source-grounded metadata validation: passed
- Production image/code files unchanged: yes

## Duplicate classifications

- visually_distinct: 24

## Production-ready images

| Candidate | Proposed name | Rights | Attribution |
|---|---|---|---|
| `00f4566964e0c88838ad` | `t71.jpg` | public_domain | None required by recorded rights status |
| `08fe3d43c27bece23076` | `t72.jpg` | attribution_required | Cropped from https://www.flickr.com/photos/jaygalvin/233397357/. Licensed under CC BY 2.0 (https://creativecommons.org/licenses/by/2.0). |
| `0b15f11312a31033907d` | `t73.jpg` | public_domain | None required by recorded rights status |
| `0b42b54c4148ee2562fb` | `t74.jpg` | attribution_required | Carl Albert Center. Licensed under CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0). |
| `0c4634d8356f42dc4e0c` | `t75.jpg` | public_domain | None required by recorded rights status |
| `0fe7ed6333faf654b062` | `t76.jpg` | public_domain | None required by recorded rights status |
| `149e63869f481474a798` | `t77.jpg` | public_domain | None required by recorded rights status |
| `168392f289b5df25f16b` | `t78.jpg` | public_domain | None required by recorded rights status |
| `253f9adb9e67db0a67d7` | `t79.jpg` | public_domain | None required by recorded rights status |
| `305d002e01745c0acd9b` | `t80.jpg` | public_domain | None required by recorded rights status |
| `36d6b17bedd4345d223e` | `t81.jpg` | public_domain | None required by recorded rights status |
| `3abe0be95aa60233f374` | `t82.jpg` | public_domain | None required by recorded rights status |
| `3e16363d7234c33cd75e` | `t83.jpg` | public_domain | None required by recorded rights status |
| `47898be6bfefc5d49c03` | `t93.jpg` | public_domain | None required by recorded rights status |
| `5202e374b764482c4cda` | `t84.jpg` | public_domain | None required by recorded rights status |
| `539626eef2896af392c4` | `t85.jpg` | public_domain | None required by recorded rights status |
| `5ac817d960fb7eb0f974` | `t86.jpg` | public_domain | None required by recorded rights status |
| `62fe0c8d105bf2ad7c95` | `t87.jpg` | attribution_required | RIA Novosti archive, image #778094, http://visualrian.ru/ru/site/gallery/#778094 35 mm film / 35 мм негатив. Licensed under CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0). |
| `6966b6853551436e849b` | `t88.jpg` | public_domain | None required by recorded rights status |
| `7720de19eda682fe608d` | `t89.png` | public_domain | None required by recorded rights status |
| `7c9a2b387a67c9bd858c` | `t90.jpg` | public_domain | None required by recorded rights status |
| `7cdcd67a8066a3b12d28` | `t94.jpg` | public_domain | None required by recorded rights status |
| `86953c03fd343ef6fcc1` | `t91.jpg` | public_domain | None required by recorded rights status |
| `f8872af0e0d18261b23e` | `t92.jpg` | public_domain | None required by recorded rights status |

## Segregated images

### Rights pending

- `03ddee84fc44a0f167c5`: Ronald Reagan Presidential Library (rights_unclear)
- `07b4ded15cd71e2d7e00`: Wikimedia Commons (rights_unclear)
- `0ee56e114d72910c951a`: Ronald Reagan Presidential Library (rights_unclear)
- `65f03237053cecb5ee11`: Ronald Reagan Presidential Library (rights_unclear)

### Maybe second pass

- `47898be6bfefc5d49c03`: Wikimedia Commons (public_domain)
- `7cdcd67a8066a3b12d28`: Wikimedia Commons (public_domain)

## Matching observations

- Material coverage improvements: `00f4566964e0c88838ad`, `0b15f11312a31033907d`, `0b42b54c4148ee2562fb`, `0fe7ed6333faf654b062`, `149e63869f481474a798`, `168392f289b5df25f16b`, `253f9adb9e67db0a67d7`, `36d6b17bedd4345d223e`, `47898be6bfefc5d49c03`, `5202e374b764482c4cda`, `5ac817d960fb7eb0f974`, `62fe0c8d105bf2ad7c95`, `7c9a2b387a67c9bd858c`, `f8872af0e0d18261b23e`
- Never competitive in the all-originals-available comparison: `08fe3d43c27bece23076`, `0c4634d8356f42dc4e0c`, `305d002e01745c0acd9b`, `3e16363d7234c33cd75e`, `6966b6853551436e849b`, `7720de19eda682fe608d`, `7cdcd67a8066a3b12d28`, `86953c03fd343ef6fcc1`
- Broad-dominance audit cases: `3abe0be95aa60233f374`
- Suspicious low-evidence pairings: 0
- Proposed higher-quality replacements: 0

## Pairing review

Reviewed 101 of 101 current proposed pairings.
Approved pairings: 17.
Existing image preferred: 83.
The two Maybe images were promoted to keep in the second-pass review.

Named people and event context were rebuilt from source captions, archive metadata and source-page evidence. The source-grounded audit status is recorded above; no facial identity inference was used. Pairing decisions remain separate from metadata validation.

Detailed results are in `pairing_review_results.json` and `pairing_review_report.md`.

```bash
python3 prepare_discovered_images.py serve-pairing-review --work-dir /disks/disk1/etc/mrsMThatcher/image_discovery_research/thatcher_image_hunt_002/integration_preparation --host 0.0.0.0 --port 8769
```

No file was copied into `images/`; no production analysis, state, receipt, history or configuration was changed.
