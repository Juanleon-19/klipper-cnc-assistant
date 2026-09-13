# Safe persistence (P1-04)

All scoped JSON writes and compensated files use `storage.safe_persistence`:
unique temporary file in the destination directory, complete write and flush,
then `os.replace`. Project/map JSON and JobRun/history additionally fsync the
file and containing directory. Regenerable plans, manifests, G-code and metadata
use atomic replacement without durable fsync. Failed temporary writes leave the
previous destination intact; temporary files are removed on exceptions.

| RESOURCE | LOCK | OWNER | ORDER |
| --- | --- | --- | --- |
| physical map repository access | existing service I/O RLock | PhysicalMapService | 0, before repository project/map locks; never acquired by storage |
| project.json, including active reference/map pointers | canonical project.json path RLock + flock | JsonProjectRepository | 1 |
| height-map JSON | canonical map path RLock + flock | JsonProjectRepository | 2, after project lock for writes |
| plan + manifest | canonical job_plan.json path RLock + flock | JobService | independent; never nested with project/map |
| compensated file + metadata, metadata enrichment | canonical metadata path RLock + flock | generator / metadata patch writer | independent; never nested with project/map |
| current_run.json | existing JobRunStore RLock + flock | JobRunStore | independent authority, never mixed with other storage locks |

Storage locks cover only reads, validation, patch/merge and filesystem writes.
No network I/O, Moonraker calls, Runtime movement, JIT rendering, sleeps, joins,
external callbacks or PhysicalMachineCoordinator acquisition occur under them.
The physical-map service's existing I/O mutex surrounds repository access only;
finalization computes outside repository locks and saves through the repository.
No map lock is held while saving a project. Nested repository order is always
project then map.

Project/map `storage_revision` is additive and defaults to zero for legacy data.
Writers merge their changes with the latest document while locked, using the
snapshot read at their revision. Independent fields and stable-id collection
items survive concurrent updates. Conflicting changes to the same field reject
the stale writer; physical tokens are indivisible. Only observation timestamps
(`updated_at`, `actualizado_en`, `last_opened_at`) combine by latest time.
Merge bases are bounded to 32 revisions per path per process. If a writer's base
has expired or was not loaded by that process, it must reload: it cannot replace
the latest state. Revisions are storage coordination, not physical trust.

Plans and manifests are built before the pair lock, then published under that
same lock with a new `generation_id`. A crash between replacements is detectable:
readers reject mismatched or unversioned pairs and rebuild. Legacy project/map
data remains readable and normalizes on its next write, without minting tokens.
Existing GET migration semantics are unchanged.

Compensated artifacts use unique immutable names. The G-code is replaced from
its temporary file before matching metadata is published as its commit marker;
an interrupted publication may leave an unreferenced G-code file, never a valid
pair. Metadata records SHA-256 of exact UTF-8 output. Consumers verify that hash
for reused and freshly generated executable artifacts, and downloads. Metadata
enrichment rereads under the artifact lock and only patches `time_estimate`,
`enrichment`, or `warnings`; hashes, provenance, generation and physical reference
authority cannot be patched. Legacy compensation formulas and FlatCAM feeds
are unchanged.

Validation commands (simulated only):

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_safe_persistence tests.test_job_run_store tests.test_job_cancel_identity tests.test_physical_reference_token tests.test_pr24_safety_integration -v
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests -v
git diff --check
rg -n 'write_text|write_bytes' src/klipper_cnc_assistant/storage src/klipper_cnc_assistant/application/physical_map_service.py src/klipper_cnc_assistant/application/compensated_gcode_service.py src/klipper_cnc_assistant/execution/job_service.py
```

The repository storage-availability probe and preserved original G-code writes
remain outside scoped persistent JSON/generated artifact writes. Settings
persistence is a separate P1 scope. No hardware/services or frontend changes.

Focused verification: 67/67 passed; a final storage-only check passed 14/14.
The physical-reference context test now reloads the map before each deliberate
token mutation, preserving all original rejection assertions while respecting
the new stale-writer contract.

One complete backend run executed 478 tests: 474 passed, with four errors in
legacy HeightMapService writes that discarded storage revisions. The legacy
model/serializer now carries its loaded revision through compute/import/update
and receives the new revision after publication. The full suite was not repeated.
After that correction, all 160 affected tests passed (storage, height maps, API,
adaptive compensation, JobRunStore, cancellation, physical tokens and PR24),
including all four previously failing cases. This is incremental revalidation,
not a claim that the original complete run was green.

Final affected-test command:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_safe_persistence tests.test_heightmap tests.test_api tests.test_adaptive_compensation tests.test_job_run_store tests.test_job_cancel_identity tests.test_physical_reference_token tests.test_pr24_safety_integration -v
```
