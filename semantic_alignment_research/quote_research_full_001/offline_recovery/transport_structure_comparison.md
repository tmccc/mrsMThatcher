# Transport Structure Comparison

The comparison uses preserved provider response dumps only; no network access was used.

| Cohort | Responses | Parsed packet | Grounding chunks | Linked grounding | Locally valid |
|---|---:|---:|---:|---:|---:|
| Developer Success | 169 | 169 | 150 | 150 | 150 |
| Vertex Success | 365 | 358 | 331 | 331 | 331 |
| Vertex Failed | 213 | 200 | 24 | 24 | 24 |

Developer responses use camelCase `groundingMetadata`; Vertex SDK dumps use snake_case `grounding_metadata`. Both layouts are supported. Failed Vertex responses split into responses with normal linked chunks and responses containing only search queries/search-entry metadata. A search-entry widget or URL in generated prose is not accepted as grounded evidence.

## Developer Success

Layouts: `{"developer_camel_case": 150, "no_grounding_metadata": 19}`

Observed grounding fields: `groundingChunks, groundingSupports, searchEntryPoint, webSearchQueries`

## Vertex Success

Layouts: `{"no_grounding_metadata": 9, "vertex_snake_case": 356}`

Observed grounding fields: `grounding_chunks, grounding_supports, retrieval_metadata, search_entry_point, web_search_queries`

## Vertex Failed

Layouts: `{"no_grounding_metadata": 17, "vertex_snake_case": 196}`

Observed grounding fields: `grounding_chunks, grounding_supports, retrieval_metadata, search_entry_point, web_search_queries`
