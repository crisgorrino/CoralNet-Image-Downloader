# CRI-12: Linear–GitHub integration test

Issue: [CRI-12 — Configure and test Linear–GitHub integration](https://linear.app/cristian-gorrino/issue/CRI-12/configure-and-test-linear-github-integration)

## Purpose

Prepare a documentation-only change to test recognition of CRI-12 in a Git
branch and a future pull request, then observe Linear linkage and configured
status automation. This file does not change application behavior.

## Local preparation — 2026-10-31

- Verified a clean working tree on `main` before creating the branch.
- Verified `origin` points to `https://github.com/crisgorrino/CoralNet-Image-Downloader.git`.
- Local `main` and the stored `origin/main` reference both pointed to
  `a6aa77cf578886df002f67320093fac24b4cacc8`; no GitHub fetch was performed.
- Fetched CRI-12 from Linear; its status was `In Progress`.
- Created `cri-12-linear-github-integration-test` from local `main`.
- Added only this documentation file; no application code was modified.

## Pending end-to-end verification

After separate authorization to commit and push:

1. Open a draft PR titled `CRI-12: Verify Linear–GitHub linking`, with a body
   referencing CRI-12 without an automatic closing keyword.
2. Confirm the native PR attachment appears on CRI-12 and the PR links to Linear.
3. Observe and record the configured Linear status transitions when the PR is
   opened and marked ready for review.
4. Record PR links and observed results in the issue. Merge only after approval,
   then verify the configured merge status transition before closing the issue.

Local preparation alone does not verify native integration installation,
webhook delivery, PR linkage, or status automation. No commit, push, PR, merge,
or Linear issue update is performed as part of this preparation.
