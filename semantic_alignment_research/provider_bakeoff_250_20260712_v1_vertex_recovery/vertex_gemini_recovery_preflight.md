# Vertex Gemini recovery preflight

- Parent: `provider_bakeoff_250_20260712_v1`
- Original exhausted cases: 23
- Already recovered without Vertex: 2
- Vertex targets: 21
- Case IDs: `['01da0feced09ed7e547a', '06c0f5db148ade11d1e3', '163c3b381d8a35535b4c', '1bd1291ebdb140b606c6', '1d75a976eec6e983f955', '2a86f5db13e31e3e99ff', '31b112d3255f4fd68671', '378fbf96d18c2e10f5a3', '4033cdcc11cd5ad48f80', '9588c850bb3b06ab3214', '95b9610a03cd601acce1', '96a9bbeeeda31eb67370', '9a19c714bcf33f9be13d', 'a6d95a012acf91c8a55e', 'b3e703fae5b1b1a34344', 'bb618dd9fc7a28e21c3a', 'c38fa0c8248f00f75587', 'dba0179df5f2f4cb4c5b', 'e931dd610a0b12b99c75', 'ed33e12a1fa6726bf8da', 'fa88be84d9caf8a1abbb']`
- Original failure: two confirmed HTTP 429 responses per case.
- Original and Vertex model: `gemini-3.1-pro-preview` (exact match; Vertex metadata resolved `publishers/google/models/gemini-3.1-pro-preview`).
- Settings: thinking budget 512, output cap 1600, JSON MIME and common response schema; no temperature override, tools, search or grounding.
- ADC: verified; project `spatial-motif-393119`; location `global`.
- Estimated input/output tokens: 44824/18900.
- Expected cost: $0.3164; conservative two-attempt cost: $0.9857.
- Ceilings: Vertex $2.00; combined recovery $2.50.

```bash
set -a; source /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env; set +a
python3 recover_gemini_vertex.py execute --execute-gemini-vertex --confirm-gemini-vertex-recovery-limit-usd 2.00 --confirm-combined-recovery-limit-usd 2.50
```
