# Historical corpus fixture

`historical-corpus-627.tar.xz` holds the exact 41 previously committed files that
the historical research tests need from
`9c142bc8485819061756a88ea99ef991a0683be0`. It is 4,598,544 bytes compressed
(67,653,601 bytes of original content). These tests deliberately retain the
original 627 completed packets, five unresolved cases, source hashes, review
decisions and trial results; current-runtime tests use the active corpus.

The file allowlist and archive SHA-256 are in `tests/helpers/historical_corpus.py`.
The helper checks the hash, exact member set, unique paths and regular-file types
before extracting into a new temporary directory. It does not call Git, download
anything or require a `.git` directory. The live posting history is represented
only by the already committed `historical_context_reply_history.reviewed.json`.
No private quotation-addition input or newly cached evidence belongs here.

Treat this as an immutable historical fixture: ordinary corpus additions must
not update it. If the historical tests need another original input, add its path
to the helper allowlist and reproduce the archive in a checkout containing the
original commit. Preserve original file bytes; fixed tar metadata and sorted
paths make the packaging deterministic:

```python
import hashlib
import io
import lzma
import subprocess
import tarfile
from tests.helpers import historical_corpus as fixture

paths = [*fixture._ROOT_FILES, *(str(fixture.RESEARCH_RELATIVE / name)
                               for name in fixture._RESEARCH_FILES)]
raw = subprocess.check_output([
    "git", "archive", "--format=tar", fixture.ORIGINAL_CORPUS_COMMIT, *paths,
])
with tarfile.open(fileobj=io.BytesIO(raw)) as source:
    content = {entry.name: source.extractfile(entry).read()
               for entry in source if entry.isfile()}
assert set(content) == set(paths)
stream = io.BytesIO()
with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as target:
    for name, data in sorted(content.items()):
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode = len(data), 0o644
        target.addfile(entry, io.BytesIO(data))
bundle = lzma.compress(stream.getvalue(), preset=6)
fixture.FIXTURE_ARCHIVE.write_bytes(bundle)
print(len(bundle), hashlib.sha256(bundle).hexdigest())
```

Update `FIXTURE_SHA256` and the documented sizes only after checking the bundle
contains precisely those historical blobs. Run the fixture regressions and the
tests importing this helper, including a source-archive run without `.git`.
The documentation checker scans all shipped non-test Python modules when Git
metadata is absent, so the normal pre-collection documentation check still runs.
