# Rollback Plan

Restore `mrsMThatcher_before.txt` atomically, retain the tombstones for audit, and do not replace the live semantic-veto manifest until a separately authorised deployment. Production state uses quote hashes; no line-index migration is required.
