# MTF corpus evidence admission report

- Production base commit: `982eda4374ef294cb4ae20b0aa40b97de3862592`
- Scope: ten human-reviewed Margaret Thatcher Foundation documents
- Decision: admit four exact-primary and six primary-variant records
- Rejected Priority C candidates admitted: **0**
- Meaning fields changed: **0**
- Network/provider/LLM/X actions: **0**

## Admission result

- Canonical packets changed: 10 (all ten reviewed quote IDs)
- Curated sources added: 10
- Public supported-field changes: 8
- Unrelated packet semantic changes: 0
- Context-only gate: 21 → 21 (no IDs changed)
- Unresolved partition: 5 → 5; membership unchanged
- Ordinary posting cycle: 611 → 611; membership and order unchanged
- Quotation IDs and public wording: unchanged

## Accepted records

| Quote ID | MTF document | Decision |
|---|---:|---|
| `36d8caf8b8ae9fbd20473e1ea33dc37aa74045e0e39efeb241c36113da822027` | 107868 | exact primary |
| `5fbdcee710fe7e18425f4eeefe811890b3c8a03803f78b23685ad11309840679` | 107332 | variant primary |
| `677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab` | 102939 | variant primary |
| `6cab54a1bfcbd9c79b72c39ff64eb7126436cde07c37d48aa6fcd9ddfec4f662` | 106689 | variant primary |
| `7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428` | 108258 | exact primary |
| `7748a7ec8ec505312e4714e9e98961453b0eecd8813a9678e28a57c332d3cd9f` | 108256 | exact primary |
| `78fac4018710af853f7eac01666370afad7551c24b604d19df3b5a710f7c5682` | 104594 | exact primary |
| `98000f36211d96c33ca0e033551ef2a7b24f56f5d624768c13abf666f5c61fe5` | 102487 | variant primary |
| `a8b53417a59ef215988e22c6c44d52e6a8401fec6ba89400001ba1e778b04855` | 105763 | variant primary |
| `b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8` | 102769 | variant primary |

## Safety and validation

- Deterministic double build: PASS (17 byte-identical stable outputs)
- Focused admission and integration validation: PASS
- Complete offline suite: **2,353 passed, 0 failed, 0 errors** (25 warnings)
- Compilation: PASS
- `git diff --check`: PASS
- Production/service state: not touched during admission

## Public-source identity handling

Two new curated documents coincided with older weak MTF locator records. The public projection now groups those records only when exactly one independently inspected curated MTF document has compatible dates and claim coverage. Internal source-role rows remain lossless; unreviewed or ambiguous locators still fail visibly.

## Deployment status

The isolated admission candidate is validated but has not yet been committed, pushed, or deployed. Deployment remains contingent on staged-diff review, a safe paused maintenance window, backup, one controlled service restart, and live health checks.

ADMISSION CANDIDATE VALIDATED — READY FOR CONTROLLED DEPLOYMENT
