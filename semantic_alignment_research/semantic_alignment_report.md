# Semantic alignment research report

No live production scoring is connected to this research pipeline.

## Inventory

- quotes_total: `632`
- quotes_needing_analysis: `496`
- active_generated_images: `79`
- images_needing_analysis: `79`
- cached_quote_fingerprints: `136`
- cached_image_fingerprints: `0`
- shortlisted_pairs_materialised: `0`
- projected_shortlisted_pairs: `9480`
- critic_pairs_needing_analysis_materialised: `150`
- projected_critic_calls_conservative: `9630`

## Cost estimate

- {'model': 'grok-4.5', 'pricing_source': 'authenticated /v1/models plus xAI cost-tracking documentation', 'usd_ticks_per_dollar': 10000000000, 'stages': [{'stage': 'quote', 'model': 'grok-4.5', 'remaining_calls': 496, 'retry_allowance_calls': 0, 'estimated_input_tokens': 446400, 'image_input_token_allowance': 0, 'estimated_output_tokens': 322400, 'reasoning_output_allowance_per_call': 250, 'max_completion_tokens_per_call': 1000, 'input_usd_per_million': 2.0, 'output_usd_per_million': 6.0, 'estimated_stage_cost_usd': 2.8272, 'hard_ceiling_usd': 6.0}, {'stage': 'image', 'model': 'grok-4.5', 'remaining_calls': 79, 'retry_allowance_calls': 0, 'estimated_input_tokens': 31600, 'image_input_token_allowance': 323900, 'estimated_output_tokens': 59250, 'reasoning_output_allowance_per_call': 250, 'max_completion_tokens_per_call': 1000, 'input_usd_per_million': 2.0, 'output_usd_per_million': 6.0, 'estimated_stage_cost_usd': 1.0665, 'hard_ceiling_usd': 3.0}, {'stage': 'critic', 'model': 'grok-4.5', 'remaining_calls': 9630, 'retry_allowance_calls': 0, 'estimated_input_tokens': 14445000, 'image_input_token_allowance': 0, 'estimated_output_tokens': 6741000, 'reasoning_output_allowance_per_call': 300, 'max_completion_tokens_per_call': 1000, 'input_usd_per_million': 2.0, 'output_usd_per_million': 6.0, 'estimated_stage_cost_usd': 69.336, 'hard_ceiling_usd': 3.0}], 'cumulative_estimated_cost_usd': 73.2297, 'hard_total_ceiling_usd': 12.0}

## Canonical reuse

- {'runs': 20, 'post_indices': 5000, 'branch_winner_records': 15000, 'candidate_sets_available': False, 'limitation': 'canonical selection records have empty candidate_detail arrays; a fourth evolving branch cannot be reconstructed from winners alone'}

## Free-trade case study

Pending independent quote/image fingerprints and critic evaluation; no unsupported verdict is fabricated.
