# Conduit development workflow

- Use `codex/networking-optimization` as the integration branch for the current work. Start new feature branches from its latest commit and merge reviewed, verified work back into it. A branch name alone does not establish ancestry.
- Existing feature branches include `codex/video-stage1` (restored Claude video work) and `codex/update-status-corner` (update navigation). Both have been integrated into the networking branch.
- The accepted application baseline is the first good Claude video implementation (`ea337e8`) plus the update-corner UI, on top of `v7.3.2-beta.1`. Run the Stage 1 launcher in Full mode when testing the accepted video changes.
- The original UDP experiment is preserved on `codex/networking-optimization-udp-backup-643d5be`. The reverted BLAKE3/large-file and later cursor work is preserved on `codex/video-stage1-regression-backup-800c868`. Preserve these histories without re-enabling their reverted code unless the user requests it.
- Keep unrelated dirty files in other checkouts intact, especially an existing modified native streamer DLL. Do not include them in a feature commit accidentally.
- A development preview is not a new published beta. Change release artifacts or publish a prerelease only when requested.

## Antigravity delegation

- Proactively use the `agy-delegator` skill for appropriate small, bounded, independently verifiable development tasks. Codex owns integration, review, and final verification.
- Keep ambiguous, architectural, security-sensitive, destructive, deployment, migration, secret-bearing, or tightly coupled work in Codex.
- Preserve concurrent edits and assign non-overlapping ownership. Never weaken permissions to delegate.
- If Antigravity is unavailable or unauthenticated, continue locally without repeatedly retrying. A failed PATH lookup alone does not establish that it is uninstalled; check execution context and known installation paths when use is allowed.
- An explicit user instruction not to use Antigravity, including a report that they disliked its work, lasts for the session unless explicitly reversed. Do not even run diagnostic invocations during that restriction.
