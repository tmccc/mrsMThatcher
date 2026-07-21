# ChatGPT Deep Research pilot

This directory contains a 25-record historical-source research pilot. It is
deliberately separate from production and does not modify quotation eligibility.

## Files to upload

Upload these three files to one ChatGPT conversation:

1. `CHATGPT_DEEP_RESEARCH_PROMPT.md`
2. `source_acceptance_policy.md`
3. `pilot_queue.json`

In the iOS app, select Deep Research from the tools menu. Use GPT-5.6 Sol Pro if
the interface permits that model for the task; otherwise use Deep Research's
current default research model. Ensure public-web research is enabled.

Send this message after attaching the files:

> Execute the attached Deep Research task exactly as specified. Research all 25
> records, inspect each cited page, and return the complete findings plus the
> required fenced JSON object. Do not omit records and do not treat search
> snippets or quotation aggregators as evidence.

Download the completed report as Markdown. The report must be independently
verified locally before any source can be accepted into the audit.
