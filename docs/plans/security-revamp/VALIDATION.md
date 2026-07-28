# DeskFlow security acceptance

Use this checklist to validate `codex/file-security-hardening` on two Windows
PCs connected to the same private network. Call them `SERVER_PC` and
`CLIENT_PC`. Use disposable files and accounts.

This checklist does not repeat accepted feature work. Mouse and keyboard
control, ordinary clipboard sync, Explorer file paste, multi-file paste,
background mode, reload, and emergency exit need only a short regression smoke
test. File queueing remains out of scope.

Do not publish IP addresses, usernames, computer names, Wi-Fi names, MAC
addresses, certificate fingerprints, clipboard contents, filenames, or
absolute paths. Redact private values before sharing results.

If DeskFlow traps input, press `Ctrl+Alt+Shift+Escape` on the server keyboard.
Stop at the first failure and record the exact step, status text, and redacted
console output.

## 0. Synchronize the security branch

The branch must exist on GitHub before this test begins. Close DeskFlow on both
PCs. Open PowerShell in the DeskFlow repository on each PC.

### On `SERVER_PC`

```powershell
git fetch origin
if (git branch --list codex/file-security-hardening) {
    git switch codex/file-security-hardening
} else {
    git switch --track origin/codex/file-security-hardening
}
git pull --ff-only
Write-Host "SERVER_PC commit:"
git rev-parse HEAD
git status --short
```

### On `CLIENT_PC`

```powershell
git fetch origin
if (git branch --list codex/file-security-hardening) {
    git switch codex/file-security-hardening
} else {
    git switch --track origin/codex/file-security-hardening
}
git pull --ff-only
Write-Host "CLIENT_PC commit:"
git rev-parse HEAD
git status --short
```

Pass when `SERVER_PC` and `CLIENT_PC` print the same full commit and both
`git status --short` commands print nothing.

## 1. Run the automated gate

Run on both PCs:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

The suite intentionally exercises rejected connections and may print expected
error-path logs. Pass only when compilation exits successfully, the final test
summary says `OK`, and `git diff --check` prints nothing.

The automated gate must include
`test_wrong_password_candidate_cannot_disconnect_live_control_session`.

## 2. Validate identity and pairing

Use a disposable DeskFlow password.

1. Clear the saved peer only through DeskFlow's **Forget saved identity and
   re-pair** action.
2. Connect for the first time.
3. Confirm only the client shows the approval dialog.
4. Confirm the short comparison code matches on both PCs.
5. Confirm the client shows a selectable full fingerprint.
6. Decline. Confirm the connection ends and no trust is saved.
7. Retry and leave the approval dialog untouched. Confirm it times out and a
   later retry prompts again.
8. Retry with the wrong password and approve the displayed peer. Confirm
   DeskFlow reports an incorrect password and saves no trust.
9. Connect with the correct password and approve. Confirm all three lanes
   connect.
10. Disconnect and reconnect. Confirm the saved peer reconnects without another
    approval dialog.
11. Use **Forget saved identity and re-pair**. Confirm the next connection
    requires approval again.

Pass when DeskFlow commits trust only after approval, password authentication,
and all three lanes connect.

## 3. Validate live-session protection

Run the focused regression five times on either PC:

```powershell
1..5 | ForEach-Object {
    .\venv\Scripts\python.exe -m unittest -q `
        tests.test_security_network.SecureControlConnectionTests.test_wrong_password_candidate_cannot_disconnect_live_control_session
    if ($LASTEXITCODE -ne 0) { throw "Live-session regression failed" }
}
```

Then connect `CLIENT_PC` normally and confirm mouse, keyboard, and clipboard
control work. Trigger `Ctrl+Alt+Shift+R` and confirm a valid reconnect can still
replace the prior control connection.

Pass when an unauthenticated candidate cannot eject the live session and an
authenticated reload still reconnects.

## 4. Validate local secret protection

After DeskFlow creates an identity, run on each PC:

```powershell
$identityRoot = Join-Path $env:LOCALAPPDATA "DeskFlow\identity"
$pointer = Get-Content (Join-Path $identityRoot "current.json") | ConvertFrom-Json
$generation = Join-Path (Join-Path $identityRoot "generations") $pointer.generation
Get-Content (Join-Path $generation "key.pem") -TotalCount 1
Get-ChildItem $generation | Select-Object Name, Length
```

Pass when:

- `key.pem` begins with `-----BEGIN ENCRYPTED PRIVATE KEY-----`;
- the generation contains `cert.pem`, `key.pem`, and
  `key-password.dpapi`;
- no plaintext private key exists in the repository root;
- peer-trust records under `%LOCALAPPDATA%\DeskFlow\peers` are protected binary
  records with hash-like filenames rather than raw peer addresses.

## 5. Validate authenticated file staging

Create a random 100 MiB file on the sending PC:

```powershell
$testRoot = Join-Path $env:USERPROFILE "Desktop\DeskFlow-Security-Validation"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$sourcePath = Join-Path $testRoot "DeskFlow-100MiB.bin"
$buffer = [byte[]]::new(1MB)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$stream = [IO.File]::Create($sourcePath)
1..100 | ForEach-Object {
    $rng.GetBytes($buffer)
    $stream.Write($buffer, 0, $buffer.Length)
}
$stream.Dispose()
$rng.Dispose()
Get-Item $sourcePath | Select-Object Length
Get-FileHash $sourcePath -Algorithm SHA256
```

Transfer it once in each direction. Record elapsed seconds, MiB/s, source hash,
and destination hash. Pass when sizes and hashes match.

During one transfer, verify encrypted staging:

1. Create a disposable text file containing a unique marker.
2. Create a destination file with the same name so Explorer opens its
   duplicate-name prompt.
3. Start the transfer and leave the prompt open.
4. On the receiving PC, run:

   ```powershell
   $staging = Join-Path $env:LOCALAPPDATA "DeskFlow\transfers"
   Get-ChildItem $staging -Recurse -File | Select-Object FullName, Length
   rg -a -l --fixed-strings "DESKFLOW-PLAINTEXT-VALIDATION-MARKER" $staging
   ```

5. Complete or cancel the prompt, wait for the transfer status to close, then
   inspect the staging directory again.

Pass when staging may contain ciphertext during the transfer, `rg` finds no
plaintext marker, the published file matches its source hash, and the job's
staging files disappear after completion or cancellation.

## 6. Validate cancellation and recovery

File queueing is intentionally out of scope. Run these four cases with the
100 MiB disposable file:

- server to client, cancel at source;
- server to client, cancel at destination;
- client to server, cancel at source;
- client to server, cancel at destination.

For each case:

1. Cancel after progress begins.
2. Confirm both peers enter cancellation and both transfer statuses clear.
3. Confirm no partial destination file or staging ciphertext remains.
4. Transfer a small disposable file in the same direction without reconnecting.

Pass when each cancellation completes once and the next transfer succeeds
without reconnecting or relaunching.

## 7. Validate network-loss recovery

1. Connect normally and transfer a small file.
2. Start a 100 MiB transfer.
3. Disable the client's network adapter.
4. Confirm both apps leave the connected state, clear transfer status, and
   restore local input within the configured timeout.
5. Re-enable the adapter and reconnect without relaunching DeskFlow.
6. Transfer text, a screenshot, one small file, and a multi-file selection.

Pass when the abandoned session cannot contaminate the new session and no stale
transfer, toast, or hidden cursor remains.

## 8. Run a feature regression smoke test

With the security branch connected:

1. Cross to the client and back.
2. Type, click, scroll, and press Delete on a disposable selected file.
3. Sync plain text, formatted text, and a screenshot in both directions.
4. Paste one file and one multi-file selection in both directions.
5. Trigger background mode, reload, and emergency exit once each.

Pass when the accepted features still work and file traffic causes no
multi-second input or ordinary-clipboard stall.

## 9. Validate clean shutdown

1. Close both apps normally.
2. On the server, run:

   ```powershell
   Get-NetTCPConnection -LocalPort 5000,5001,5002 -ErrorAction SilentlyContinue
   ```

3. Confirm no unexpected DeskFlow Python process remains.
4. Reopen both apps, reconnect using saved trust, and transfer one small file.
5. Close both apps again.

Pass when the ports close, no stale process remains, saved trust reconnects
without a new prompt, and the final transfer succeeds.

## Results

Record one result block:

```text
Full commit on SERVER_PC:
Full commit on CLIENT_PC:

0 Branch synchronization: PASS/FAIL/NOT RUN
1 Automated gate: PASS/FAIL/NOT RUN
2 Identity and pairing: PASS/FAIL/NOT RUN
3 Live-session protection: PASS/FAIL/NOT RUN
4 Local secret protection: PASS/FAIL/NOT RUN
5 Authenticated file staging: PASS/FAIL/NOT RUN
6 Cancellation and recovery: PASS/FAIL/NOT RUN
7 Network-loss recovery: PASS/FAIL/NOT RUN
8 Feature regression smoke test: PASS/FAIL/NOT RUN
9 Clean shutdown: PASS/FAIL/NOT RUN

Transfer direction:
Source size:
Destination size:
Source SHA-256:
Destination SHA-256:
Elapsed seconds:
MiB/s:

First failure:
Expected:
Observed:
Exact status text:
Relevant redacted console lines:
Reconnect or relaunch required: YES/NO
```

Security acceptance is complete only when both PCs test the same full commit
and every required section passes.
