# Machine-aware Client-to-Client file-paste routing

## Problem

Conduit detects and relays file clipboard offers from either Client, but a
paste from Client A to Client B never starts. The Server's paste coordinator
still models topology as two endpoint roles: `server` and `client`. It reduces
both Client A and Client B to `client`, so it evaluates a Client-to-Client
paste as `client -> client` and treats it as a local Windows paste. The Server
forwards Ctrl+V to Client B instead of sending `file_paste_intent`; the
existing manifest and encrypted file-frame relay never receives a request.

The global clipboard hub already preserves the stable source machine ID, and
the input router already exposes the active destination machine ID. The bug is
therefore in paste-route selection, not clipboard capture or file transport.

## Decision

Keep the existing clipboard and file-transfer wire protocols. Make only the
Server's cluster-mode paste decision machine-aware.

When the Server installs or refreshes a paste route, it derives:

- the source machine from `ClipboardHub.latest_item.source_id`;
- the destination machine from `InputRouter.active_machine_id`, or the Server
  machine ID while the cursor is local;
- whether transfer is required from `kind == "files"` and
  `source_machine_id != destination_machine_id`.

`PasteCoordinator.set_route` will accept an optional, explicit transfer
decision. Existing callers that omit it retain the current role-based
`server`/`client` behavior. The Server supplies the override only when its
current clipboard offer and latest hub item describe the same cluster
revision. A missing or mismatched identity does not invent a route.

This keeps the coordinator's key suppression behavior in one place. When the
cursor is on Client B and the latest files came from Client A, the Server
suppresses native Ctrl+V and sends one `file_paste_intent` to B. Client B then
uses its existing remote-offer state to request the manifest. The Server's
existing `ClusterFileRouter` relays manifest control messages and encrypted
file frames between A and B.

## Required behavior

- Client A -> Client B and Client B -> Client A start remote file paste.
- Client A -> Client A remains a native Windows paste with no cluster job.
- Server -> either Client and either Client -> Server remain unchanged.
- Ordinary clipboard paste remains native and does not start file transfer.
- The Server targets only the Client that owns the cursor.
- Source and destination identity stay latched by the existing cluster job
  once the destination requests the paste.
- Missing, stale, or inconsistent route metadata fails closed without sending
  a file-paste intent to the wrong machine.

## Scope

In scope:

- machine-aware route selection in the Server;
- a narrow PasteCoordinator API extension for an explicit decision;
- diagnostic logging that names sanitized machine/session identifiers;
- regression and real-TLS system coverage for both Client directions.

Out of scope:

- new ports or direct Client-to-Client sockets;
- changes to TLS framing, file chunks, manifests, Explorer publication, or
  clipboard history;
- storing Client-to-Client file bytes on the Server;
- changes to topology or cursor ownership.

## Failure handling

The destination must not receive an intent unless the Server can resolve a
ready active Client and match the current offer to the latest hub revision.
Failures after the request use the existing cluster job behavior: notify the
involved endpoints, cancel the job on disconnect, and leave unrelated
clipboard and file jobs untouched.

## Verification

Add tests that first reproduce the current failure, then prove:

1. A file offer from Client A plus cursor ownership on Client B suppresses the
   forwarded Ctrl+V and targets B with `file_paste_intent`.
2. Reversing A and B behaves identically.
3. Copying and pasting on the same Client remains native.
4. Server-to-Client and Client-to-Server routes retain their current result.
5. Ordinary clipboard offers never trigger the file path.
6. A real-TLS two-Client seam proceeds from file offer through paste intent,
   manifest exchange, acknowledgement, and at least one relayed file frame.
7. Focused file/clipboard tests, the full suite, compileall, and
   `git diff --check` pass.

Physical acceptance uses `run.bat` on the Server and both Clients. Test one
small file and one folder in both Client directions, repeat after reversing
the topology, and confirm that same-machine paste still uses Windows directly.
