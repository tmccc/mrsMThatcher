# Improved Challenger Policy

Priority: (1) exact production-decision runner-up, (2) cached runner-up under identical eligibility/state, (3) genuinely eligible soft-light alternative, (4) semantic runner-up above both semantic and quality floors, (5) first-impression runner-up above a semantic floor, otherwise `no_credible_challenger`.

This implementation currently enables only priority 1 because no auditable soft-light candidate trace or complete cached semantic ranking exists. A candidate must be active, generated, identity-eligible in the trace, independently first-impression fingerprinted, non-duplicate by SHA-256, editorial quality >=70, production score >=40, and topic component >=25. Human labels are never inputs. These floors are safety constraints, not fitted thresholds.
