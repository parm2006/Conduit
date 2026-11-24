# Conduit development workflow

- Use `codex/networking-optimization` as the integration branch for the current work. Start new feature branches from its latest commit and merge reviewed, verified work back into it. A branch name alone does not establish ancestry.
- Existing feature branches include `codex/video-stage1` (restored Claude video work) and `codex/update-status-corner` (update navigation). Both have been integrated into the networking branch.
- The accepted application baseline is the first good Claude video implementation (`ea337e8`) plus the update-corner UI, on top of `v7.3.2-beta.1`. Run the Stage 1 launcher in Full mode when testing the accepted video changes.
- The original UDP experiment is preserved on `codex/networking-optimization-udp-backup-643d5be`. The reverted BLAKE3/large-file and later cursor work is preserved on `codex/video-stage1-regression-backup-800c868`. Preserve these histories without re-enabling their reverted code unless the user requests it.
- Keep unrelated dirty files in other checkouts intact, especially an existing modified native streamer DLL. Do not include them in a feature commit accidentally.
- A development preview is not a new published beta. Change release artifacts or publish a prerelease only when requested.

## Feature completion and test launches

- After completing a feature, automatically open the exact Conduit version being tested locally; do not wait for the user to request it. Gracefully close an older test instance if needed, preserve unrelated processes, and verify the new app starts.
- Always include copy-paste PowerShell commands in the final response for both this PC and the other PC: change directory, synchronize the correct test branch where needed, install changed dependencies where needed, and launch the tested mode. State which branch/version is being opened.
- The other PC's repository is `C:\Users\parth\Projects\Conduit`. This PC's main checkout has the same path, but the current isolated test checkout is `C:\Users\parth\.codex\worktrees\video-stage1\Conduit`; use the actual checkout containing the feature rather than assuming the main checkout has it.
- Currently use `scripts\run_video_stage1.ps1 -Mode Full` for the accepted video test configuration. When using this isolated checkout, its interpreter is `C:\Users\parth\Projects\Conduit\venv\Scripts\python.exe`. On the other PC use its repository's `venv\Scripts\python.exe`.

## Antigravity delegation

- Proactively use the `agy-delegator` skill for appropriate small, bounded, independently verifiable development tasks. Codex owns integration, review, and final verification.
- Keep ambiguous, architectural, security-sensitive, destructive, deployment, migration, secret-bearing, or tightly coupled work in Codex.
- Preserve concurrent edits and assign non-overlapping ownership. Never weaken permissions to delegate.
- If Antigravity is unavailable or unauthenticated, continue locally without repeatedly retrying. A failed PATH lookup alone does not establish that it is uninstalled; check execution context and known installation paths when use is allowed.
- An explicit user instruction not to use Antigravity, including a report that they disliked its work, lasts for the session unless explicitly reversed. Do not even run diagnostic invocations during that restriction.
