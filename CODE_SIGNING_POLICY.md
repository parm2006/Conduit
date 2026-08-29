# Code Signing Policy

## Status: Application pending submission

Conduit is applying to the SignPath Foundation open-source program to use SignPath.io for future official release signing. Conduit 6.0.1 and all earlier public binaries are unsigned. This policy does not claim that the application has been submitted or approved, or that any current download has a trusted signature.

If SignPath Foundation approves the application and the integration becomes operational, signed release documentation will use the following attribution:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

The release notes for each future release will state whether its artifacts were signed.

## Scope

This policy covers official Windows release artifacts built from an exact tagged revision of the [Conduit source repository](https://github.com/parm2006/Conduit). It does not cover development builds, forks, repackaged downloads, or binaries distributed by third parties.

Conduit will request signatures only for project-owned release artifacts. Dependencies bundled into an official Conduit build remain subject to their own licenses and publishers; Conduit will not submit standalone third-party software for signing.

## Intended release process after approval

1. A GitHub-hosted runner checks out the exact tagged source revision and runs the automated test suite.
2. PyInstaller builds the inner `Conduit.exe` artifact.
3. SignPath signs the inner executable through the approved trusted-build configuration.
4. NSIS packages the signed executable into the Windows installer.
5. SignPath signs the completed installer through the approved trusted-build configuration.
6. The release process verifies both Authenticode signatures before producing SHA-256 checksums, provenance, and release assets.
7. A maintainer manually approves each SignPath signing request before publication.

The SignPath workflow and its identifiers will be added only after SignPath supplies the approved organization, project, signing-policy, artifact-configuration, and trusted-build configuration values.

## Roles and review

Conduit is currently maintained by [@parm2006](https://github.com/parm2006), who acts as the project committer, reviewer, and signing approver. External contributions require maintainer review before merge. Release, installer, workflow, signing-policy, and privacy-policy changes are covered by [CODEOWNERS](.github/CODEOWNERS).

Repository access and SignPath access must use multi-factor authentication. Each signing request must correspond to reviewed source in the official repository and receive explicit approval; signing is never granted automatically merely because a build completed.

## Certificate and secret protection

The code-signing certificate and private key remain protected by SignPath's signing infrastructure and are not exported to the repository or build runner. SignPath credentials and API tokens are stored only as encrypted GitHub Actions secrets. They must never be committed, embedded in artifacts, printed in logs, or shared in issue reports.

## Verification

For a release identified as signed, users can inspect an artifact in Windows file properties or run:

```powershell
Get-AuthenticodeSignature .\Conduit-Setup.exe | Format-List Status,StatusMessage,SignerCertificate,TimeStamperCertificate
```

The expected `Status` is `Valid`. Users should also compare the file's SHA-256 hash with the `SHA256SUMS.txt` file attached to the same [GitHub release](https://github.com/parm2006/Conduit/releases). A valid signature confirms publisher and file integrity; it does not guarantee that antivirus or SmartScreen will never display a warning.

## Reporting concerns

Report suspected signing abuse, compromised release artifacts, or security vulnerabilities using the private reporting instructions in [SECURITY.md](SECURITY.md). Do not include passwords, private keys, pairing codes, clipboard contents, or other sensitive data in a public report.

See [PRIVACY.md](PRIVACY.md) for Conduit's data-handling policy.
