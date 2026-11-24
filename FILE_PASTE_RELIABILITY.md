# File paste preparation and first-attempt reliability

File manifest preparation now has a five-second maximum wait. Successful preparation proceeds immediately; there is no artificial five-second cooldown. On expiry the destination shows **Failed to hash in 5 seconds**, releases its pending-paste latch, and notifies the cluster router to release that request's slot. Late responses are ignored. Source preparation that completes after its own five-second budget is discarded instead of starting a late transfer.

Production manifest requests reserve a bounded preparation slot and hash on a background worker. This keeps control callbacks available while reading files. A timed-out worker may finish its current disk/hash operation in the background; the timeout does not forcibly terminate a Python thread. Failed or obsolete preparation releases its reserved slot. Source file selection retries a transient Windows clipboard-open failure up to five attempts, 20 ms apart.

The first-paste race occurred when an accepted remote file offer was waiting for the asynchronous clipboard publication worker. Ctrl+V could observe the new hub revision with the old route and fall through to a native paste. Accepted file offers now update routing synchronously. Revision acceptance, route updates and clipboard publication use the existing paste-route lock so an older in-flight ordinary update cannot overwrite a newer file offer. Intentional separate Ctrl+V presses still remain distinct FIFO paste requests.

Regressions cover the five-second deadline, one failure notification, ignored late responses, discarded late source preparation, nonblocking hashing, transient clipboard locks, authorized routed-timeout cleanup, a paused hub worker, and concurrent old/new clipboard offers.

On both PCs, run the same test branch in Full mode. Copy a small file and paste immediately once; repeat with folders and in both server/client arrangements. Then test progressively larger files. A preparation taking over five seconds should show the failure toast without starting a later copy. A retry after failure should remain possible.

This change does not alter file size constants, file checksum algorithms, wire formats, video or encryption. File preparation still uses the existing SHA-256 manifest checksums. The practical size cutoff depends on file count, disk speed, cache state and CPU; a binary search measures this timeout boundary on the tested machine, not a universal byte limit.
