# Source provenance

Assembled from server snapshots on 2026-11-15. The main mechanism and runner come from matching frozen experimental sources rather than the top-level development copy.

- Main: useful_residual_v6_pool_v10_2_prompt_chain, protected by V10_SOURCE_LOCK.json and the underlying runtime manifest.
- Baselines: BIRD conversational adapters with upstream commit and prompt hashes.
- Ablations: executable four-arm snapshot, with final recovery adapters archived separately.
- Evaluation: order-sensitive scoring, strict P audit and native reconciliation.
- Construction: flat-disclosure package and supporting modules.
- P expansion: separate post-submission extension.

Public wrappers adapt paths and entrypoints. Frozen algorithm files keep original bytes and names. Archived deployment paths use placeholders. The SQL wrapper derives its root from this repository. Packaging changes are recorded in supplementary/provenance/.

Historical modules remain where required by hash manifests. Removing them breaks integrity checks. RELEASE_MANIFEST.json hashes publishable files; caches and local runs are excluded. The validation report records actual checks.
