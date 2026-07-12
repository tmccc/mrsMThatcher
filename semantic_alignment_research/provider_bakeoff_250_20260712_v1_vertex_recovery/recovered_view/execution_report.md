# 250-case four-provider critic execution report

- Manifest cases: 250
- Four-provider complete cases: 250
- Missing: {'grok': 0, 'openai': 0, 'anthropic': 0, 'gemini': 0}
- Combined known spend: $16.873248
- Wall-clock seconds: None
- Meta automatic coverage: 210/250
- Disagreement bands: {'moderate': 174, 'low': 62, 'high': 14}

## Provider completion

- grok: completed=250, exhausted=0, attempts=250, cost=$2.840154, elapsed=2936.7361755371094, uncertain exposure=$0.000000
- openai: completed=250, exhausted=0, attempts=250, cost=$7.252130, elapsed=3111.7142572402954, uncertain exposure=$0.000000
- anthropic: completed=250, exhausted=0, attempts=250, cost=$4.311918, elapsed=3921.575160741806, uncertain exposure=$0.000000
- gemini: completed=227, exhausted=23, attempts=273, cost=$2.469046, elapsed=1755.8301267623901, uncertain exposure=$0.000000

## Safety

Fingerprints were reused. The original 25 are preserved as regression cases. No tools/search were enabled. No production files or behaviour were changed.
