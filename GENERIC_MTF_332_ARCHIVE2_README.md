# Prepared 332-case local MTF remediation assessment

This tool prepares the preserved 332-quotation, 170-document generic-context
assessment for an operator-supplied local MTF mirror. The mirror and the three
hash-bound preserved assessment inputs are private capabilities outside Git;
their locations are not embedded in this repository.

The target set is taken from the earlier immutable assessment artefact. It is
not re-derived from current public rendering because production now
intentionally suppresses the generic Context sentence.

The wrapper supports numeric document pages saved as extensionless files,
`.html` files, or legacy percent-encoded flat filenames. It verifies stable
content-bound bytes for all locally present target documents before and after
execution. Network access is explicitly blocked.

The assessment has not been run.

When authorised, use a new private output directory:

```bash
python3 run_generic_mtf_332_archive2_assessment.py \
  --execute \
  --preserved-run-dir /path/to/private/preserved-assessment-run \
  --mirror-www-root \
  /path/to/private/Thatcher-archive-2/www.margaretthatcher.org \
  --output-dir \
  /path/to/private/output/<timestamp>-generic-mtf-332-archive2
```

The run remains advisory. It writes only to the supplied private output
directory and performs no evidence admission or canonical mutation.
