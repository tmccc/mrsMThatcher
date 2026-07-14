# Scene Grammar Planning 001

- Executable planning set: yes
- Deterministic scene specifications: 20
- Source cases: Generation Prompt Pilot 001
- Inputs: cached quote semantics, cached visual-intent briefs, Tony's prior notes and offline failure analysis
- Everest correction: tallest Everest summit, climber and Union Flag; communist/map/Cold War imagery forbidden
- Free-trade correction: voluntary exchange and real goods/payment; generic ideological symbolism forbidden
- Prompt compilation: not performed
- Image generation: not performed
- Provider calls: none

## Review command

```bash
python3 tools/scene_grammar_planning_review.py \
  --run-dir semantic_alignment_research/scene_grammar_planning_001 \
  --host 127.0.0.1 --port 8774
```

The reviewer records approve, revise or reject plus optional notes using atomic persistence. It binds only to loopback and supports previous/next navigation, progress, revision and resume.

No production file or behaviour was changed. Nothing was staged, committed, pushed or deployed.
