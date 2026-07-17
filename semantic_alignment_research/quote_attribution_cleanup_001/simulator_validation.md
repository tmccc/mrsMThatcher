# Reduced-Corpus Simulator Validation

Passed: True
Full sweep: {'events': 47814, 'distinct_active_quotes': 613, 'decision_counts': {'allow': 47119, 'veto': 677, 'unknown': 18}, 'known_winner_coverage': 0.9996235412222362, 'unknown_winner_rate': 0.00037645877776383487, 'veto_reason_counts': {'required_participant_identity_not_source_grounded': 78, 'required_relationship_not_source_grounded': 60}, 'removed_quote_selection_count': 0, 'unresolved_quote_selection_count': 0, 'unexpected_quote_selection_count': 0}
Monte Carlo: {'events': 194929, 'distinct_active_quotes': 613, 'decision_counts': {'allow': 193276, 'veto': 1198, 'unknown': 455}, 'known_winner_coverage': 0.9976658167845729, 'unknown_winner_rate': 0.0023341832154271554, 'veto_reason_counts': {'required_relationship_not_source_grounded': 128, 'required_participant_identity_not_source_grounded': 123}, 'removed_quote_selection_count': 0, 'unresolved_quote_selection_count': 0, 'unexpected_quote_selection_count': 0}
Boundary stress: {'events': 3510, 'distinct_active_quotes': 23, 'decision_counts': {'allow': 3510}, 'known_winner_coverage': 1.0, 'unknown_winner_rate': 0.0, 'veto_reason_counts': {}, 'removed_quote_selection_count': 0, 'unresolved_quote_selection_count': 0, 'unexpected_quote_selection_count': 0}

The validation was fully offline and opened the freshly generated reduced-corpus simulation database read-only.
