# Hashing inventory

`app.hashing.new_hasher(data=b'')` supplies incremental BLAKE3 for interchangeable, local digests. It supports `update`, `digest` and `hexdigest` without changing call-site buffering.

| Purpose | Implementation | Reason |
| --- | --- | --- |
| Clipboard snapshot duplicate detection | BLAKE3 | Computed locally for captured and injected snapshots; not serialized in clipboard messages. |
| Session lane-token lookup | BLAKE3 | Issuance and verification hash the same random token locally; the raw token is transported over the existing authenticated lane. |
| One-use file-lane token comparison | BLAKE3 | Both candidate and expected token are hashed locally; constant-time comparison and one-use handling remain. |
| Loaded native DLL diagnostic fingerprint | BLAKE3 | Logging only; labelled `blake3`. |
| File preparation and received-file checksums | SHA-256 | Existing manifests explicitly serialize `sha256`. BLAKE3 needs a separate wire-format/capability migration. |
| Certificate fingerprints and paired-peer verification | SHA-256 | Existing saved fingerprints and pairing semantics. |
| Certificate signatures | SHA-256 through the cryptography certificate API | Certificate signing is not an interchangeable content-hasher call. |
| Stable machine IDs | SHA-256 | Existing topology/device identifiers must remain stable. |
| Trust-record filenames | SHA-256 | Existing persisted records must remain discoverable. |
| Updater download, staged installer and final installer verification | SHA-256 | Must match published `SHA256SUMS.txt` and stored verified-download metadata. |

Build/release checksum generation, SHA-256 test fixtures for fixed formats, and the documented original Claude DLL checksum remain SHA-256. Video AES-GCM authentication, file staging encryption and TLS are unchanged. There are no timeout, file-size, buffering or transfer-protocol changes in this migration.

Changing `new_hasher` affects local digests only. Persistent identities and named external checksum formats intentionally continue to select their required algorithm explicitly. Install updated requirements before launching source copies; the release dependency list also pins BLAKE3 for future packaging.
