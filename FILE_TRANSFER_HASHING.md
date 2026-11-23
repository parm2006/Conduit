# File-content hashing on the large-file test branch

Verification: all 1,074 regression tests passed. Random-file transfer probes at
500 MiB and 1 GiB passed for both SHA-256 and BLAKE3. A fresh 5 GiB BLAKE3 probe
prepared and accepted its manifest in 1.7087 seconds, delivered/acknowledged
20,480 chunks, and verified all 5,368,709,120 decrypted bytes. Transfer plus
readback took 56.7423 seconds locally. These are in-process lane tests, not a
physical two-PC Explorer result or network throughput benchmark.
The same fresh 5 GiB probe forced to SHA-256 took 12.6478 seconds to prepare and
was correctly rejected by the 3-second deadline before transfer. Update both
endpoints (and any relay) to use BLAKE3 for this large-file path; SHA-256 fallback
preserves compatibility, not an assurance of meeting the short deadline.

The expanded comparison recorded 342 measurements over 500 MiB, 1 GiB, and
5 GiB random files, using three read-buffer sizes and mapped modes. Median
5 GiB preparation was 12.099 seconds for 256 KiB SHA-256 streaming and 1.745
seconds for the production parallel BLAKE3 snapshot. XXH3-128 streaming took
1.417 seconds, but is explicitly excluded from the app. The raw measurements,
minimum/median/maximum, entropy checks, and earlier slower random-file runs are
retained in `build/hash-preparation-profile/ALL_RANDOM_REPORT.md` and its JSON.

Upgraded endpoints advertise `supported_hash_algorithms: ["blake3", "sha256"]`
in the file-manifest request. The source chooses BLAKE3 when supported; missing
capabilities identify a legacy request and select SHA-256. The cluster router
preserves the destination's capabilities. An older router strips the new
capabilities, which safely selects the legacy SHA-256 format.

BLAKE3 manifests explicitly contain `hash_algorithm: "blake3"` and per-file
`digest` fields. SHA-256 retains the original wire format, including per-file
`sha256`, without a new required field. Unknown algorithms and ambiguous digest
fields are rejected. Internally the field is named `digest`. Sender validation
checks both the digest and its algorithm against the source snapshot. The
receiver computes the matching algorithm and verifies it before finalizing
encrypted staging. Neither a non-cryptographic hash nor a silent interpretation
of BLAKE3 bytes as SHA-256 is permitted.

BLAKE3 preparation uses the library's parallel memory-mapped implementation,
the fastest cryptographic path measured in the profiling harness. On Windows,
a deny-write/delete file handle is held while mapping and hashing to protect
against concurrent truncation or mutation of the mapped file. Existing file
identity/size/time checks remain. A file already open for writing can therefore
fail preparation with a source-changed error; close its writer before copying.
Transfer chunks remain 256 KiB. Incremental destination hashing uses
single-threaded BLAKE3, avoiding parallel dispatch on small chunks. The pinned
dependency is `blake3==1.0.11`. Authentication, certificate fingerprints, updater
hashes, transport encryption, and encrypted cache records are unchanged.

Preparation remains on bounded background workers. The manifest response has a
hard **3-second deadline**, as requested. That is an expiry, not a startup wait:
the transfer starts as soon as preparation succeeds. Expiry produces the toast
“File preparation exceeded 3 seconds.” and releases the cluster routing slot.
The 30-second acknowledgement/cache expiry and transfer no-progress deadlines
are separate and unchanged.

This short deadline does not guarantee 5 GiB support on every disk. Fully written
random-file measurements have exceeded three seconds for 5 GiB, even with
BLAKE3; such copies will be rejected before transfer. Raising the deadline or
designing progress-aware preparation would be a separate choice.

The debugger probe now creates fully written random binary files:

```powershell
python -m scripts.debug_large_file_paste --size-mib 500 --hash-algorithm blake3 --transfer
python -m scripts.debug_large_file_paste --size-mib 500 --hash-algorithm sha256 --transfer
```

It exercises manifest preparation/acceptance, actual bounded chunk delivery,
encrypted staging, and decrypted readback verification. Control and file-lane
messages are dispatched in process; it does not alter the Windows clipboard or
claim a physical two-PC Explorer test. Temporary files are cleaned up.

Hash benchmarks use a fresh `os.urandom` block on every write, flush and fsync
the file before timing, verify repeatable digests, and record entropy and sample
compression ratio. No system-wide cache eviction is performed. Results must
not be described as guaranteed cold-disk throughput.

XXH3-64/128, CRC32C, wyhash, rapidhash, and BLAKE2 are benchmark comparisons only.
The project accepts only BLAKE3 and SHA-256. The isolated benchmark dependencies,
native reference wrappers, every measurement, minimum/median/maximum summaries,
and reports live under `build/hash-preparation-profile/`. No prerelease is
published by this change. Physical testing should use the updated source on
both PCs, retaining the previously tested `Full` video mode, and verify file
contents and input/video responsiveness while preparation runs.
