# GitHub Delivery and Evidence

The current repository is [Pitohuie-AIversion/HydroDisturb-Bench](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench). Its configured `origin` is `https://github.com/Pitohuie-AIversion/HydroDisturb-Bench.git`. Routine delivery updates this existing repository; it does not create another repository or change its visibility.

## Routine delivery

Check the checkout, branch, remote, and incoming changes before preparing a commit:

```powershell
git status --short
git branch --show-current
git remote -v
git fetch origin
git log --oneline --left-right HEAD...origin/main
```

Preserve others' remote changes when synchronizing the branch. Do not force-push over them. If code changed between an experiment and delivery, identify the code actually executed, retain its hashes, and record the subsequent validation separately. The [amplitude-series report](reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md) documents this distinction for its computation and later synchronization with remote `main`.

Stage the intended paths explicitly, then inspect the changes and staged bytes:

```powershell
git diff --check
git diff --cached --check
git diff --cached --stat
git status --short
```

Run checks appropriate to the change and retain their exact commands, exit codes, test counts, and code revision. Historical Windows test logs are evidence of those executions, not a claim that a fresh clone has rerun them. Some unmarked legacy tests require ignored local model and diagnostic files: `pytest -m "not integration"` excludes marked integration tests but is not a guarantee of data-free execution. See [development instructions](DEVELOPMENT.md) and [validation scope](VALIDATION.md).

Commit only the reviewed changes, then push the intended branch. For an authorized direct delivery on `main`, the command is `git push origin main`; a pull-request branch should be pushed under its own name. Verify the resulting remote commit and provide fixed-commit links to the report and evidence. Archiving or pushing completed results does not require rerunning CFD.

## Lightweight evidence is the normal delivery

New deliveries keep each added or modified Git file at or below **1 MiB**. This is the current delivery rule, not a claim that every historical repository file meets that limit. Commit source, configuration, tests, compact readbacks, metrics, logs, suitable small figures, and identity manifests.

Solved MPH files, full native vectors, dense TXT/NPZ fields, and large archives remain local unless a separate data publication is arranged. Record their paths, sizes, SHA-256 hashes, and relationships as `local_only`; do not create a large ZIP merely to move the same payload into Git. A hash manifest provides identity, not a backup. The amplitude-series delivery records that an independent backup was not configured.

Compact readbacks must say that they are summaries and that full originals remain local. For omitted large arrays or encoded vectors, retain counts and hashes with an explicit hash basis. Preserve actual expressions, units, Dataset links, solver settings, and native-state hashes. When a model hash comes from the recorded execution, identify that source rather than claiming a new read of a multi-gigabyte model. Keep source-force integrals and impulses distinct from a net-fluid-momentum claim.

The [lightweight export helper](evidence/m3_pulse_amplitude_series_20261106/export_lightweight_evidence.py) extracts solver text from the actual process stdout before its structured JSON response. It requires the `Step Time Stepsize` table header, normalizes LF and horizontal trailing whitespace, and records source identities. A truncated solver getter is not a substitute for that process log. The report should identify completion and any failed attempts separately.

### Git byte integrity

Evidence defaults to `-text`; only the extensions explicitly listed in [`.gitattributes`](../.gitattributes) are normalized to LF. PNGs and unknown binary formats must retain their bytes. When changing these rules or checking a delivery, use the real staging/blob checks:

```powershell
python .\check_git_attributes.py --attributes .\.gitattributes
python -m pytest -v tests/test_git_attributes.py
```

Hash manifests must distinguish source-file bytes from Git-normalized bytes. Preserve historical evidence and manifests; investigate a mismatch rather than rewriting an old artifact to make the check pass.

### Portable audit of the amplitude-series delivery

This audit reads the fixed delivery's Git objects and needs no COMSOL installation, MPH model, or full field arrays:

```powershell
python .\docs\evidence\m3_pulse_amplitude_series_20261106\audit_delivery.py --git-ref 195d613decee0f960b86f2dd588592f60fc01856 --output .\amplitude_delivery_audit_01.json
```

Use a new output path outside the evidence folder. The auditor checks [the delivery manifest](evidence/m3_pulse_amplitude_series_20261106/delivery_manifest.json), changed Git files, byte sizes and hashes, required plans/readbacks/logs/analysis, and explicit summary labels. It does not repeat the full native-state or full-field numerical audit.

Use `--git-ref HEAD` only when `HEAD` is that experiment's delivery commit. Later documentation commits have a different parent and are not the same delivery. The auditor also accepts `--git-ref INDEX` for a staged delivery whose recorded `delivery_parent_commit` equals the current `HEAD`; the frozen physical-plan `base_commit` can be older. Manifest hashes describe actual normalized Git bytes. Exclude the manifest itself and the audit result from the manifest to avoid self-reference.

## Historical bootstrap scripts

[`scripts/publish.ps1`](../scripts/publish.ps1) and [`scripts/publish.sh`](../scripts/publish.sh) are historical scaffold bootstrap helpers. They expect the old `general-chen` account, attempt to create a private repository, and reject an existing repository. They are not the update path for the current `Pitohuie-AIversion` repository.

Keep credentials, private keys, and COMSOL license material out of Git. Local paths in provenance records explain where an execution occurred; they do not make those files remotely available.
