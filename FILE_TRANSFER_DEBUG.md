# Large file paste: preparation timeout

Current branch update: upgraded peers negotiate BLAKE3 for file-content hashes;
legacy peers retain SHA-256. The preparation deadline is now a hard **3 seconds**
at the user's request. Hashing still runs off the control thread. A timeout now
produces a visible failure toast and releases the routed request. It is not an
artificial delay before starting a transfer. See `FILE_TRANSFER_HASHING.md`.
The debugger fixture now writes fresh `os.urandom` bytes rather than zeros.
The historical measurements below describe the initial timeout fix.

The reported practical limit around 381.5 MB was reproduced as a preparation
timeout, rather than a configured byte limit. The existing manifest validation
permits files and selections up to 1 TiB.

Before sending a manifest, `SourceFile.snapshot` hashes every source byte in
256 KiB chunks. The destination previously allowed only one second for the
manifest response. A real 500 MiB (524,288,000 byte) fixture took 1.2719 seconds
to prepare under Python's `pdb`. At `ManifestHandshakeQueue.accept`, the request
was already `timed_out` and removed from the lookup. Consequently, the receiver,
clipboard publisher, and transfer executor were never invoked. This accounts
for the lack of a transfer toast.

`FilePasteService` now allows 120 seconds for preparation. Production client and
server handlers queue preparation on daemon workers, with at most eight combined
preparations and unacknowledged outgoing manifests. Hashing does not block the
control callback thread. Preparation results are discarded if the connection
has disconnected or its generation changed. The existing chunk sizes,
encryption, integrity checks, and byte limits remain in effect.

The same debugger probe accepted a 500 MiB manifest after the change. Its optional
data test delivered and acknowledged 2,000 chunks through the production sender
and receiver, finalized encrypted staging, and verified the SHA-256 of all
524,288,000 bytes read back through the receiver. Transfer and decrypted readback
took 5.228 seconds locally. The fixture is zero-filled and compressible; this
timing is not a network throughput benchmark.

Reproduce from the repository root with its Python environment:

```powershell
python -m scripts.debug_large_file_paste --size-mib 500 --transfer
python -m unittest tests.test_large_file_preparation tests.test_file_paste_service tests.test_file_paste_handshake
```

The probe uses real hashing, authenticated encrypted staging, and chunk
acknowledgements, with in-process control and file-lane dispatch. It does not
open sockets, inject a paste, or alter the Windows clipboard. Temporary source
and cache files are cleaned up afterward. The optional `--read-delay-ms` flag
can simulate slow source reads; the recorded 500 MiB reproduction used no
injected delay. Platforms restricting hard links may require running the probe
outside their sandbox because staging finalization uses `os.link`.

Verification: all 1,056 Python regression tests passed. Real Explorer and
two-machine network testing remain necessary. Use the updated source on both
machines, keep the previously tested video mode (`Full`), and copy a 500 MB file
in each direction. Confirm a transfer toast, successful Explorer completion, and
matching source/destination hashes (`Get-FileHash`). Then try a folder selection
above the former threshold. A slow selection can still exceed the bounded
120-second preparation deadline; larger capacities have not yet been physically
verified. Ordinary text/image clipboard limits and prerelease publication are
outside this change.
