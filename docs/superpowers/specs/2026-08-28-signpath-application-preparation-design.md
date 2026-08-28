# SignPath Application Preparation Design

## Goal

Prepare Conduit's public repository for an honest SignPath Foundation open-source
application without implying that current releases are signed or that the
application has already been approved.

## Current state

- Conduit is public and licensed under GPL-3.0-only.
- The repository publishes Windows installers and corresponding source archives.
- Release 6.0.1 is unsigned and Microsoft Defender currently detects its installer
  as `Trojan:Win32/Wacatac.C!ml`.
- The release script supports local signing hooks, but the repository has no
  GitHub Actions build or SignPath integration yet.
- Public adoption is early: the repository currently has two stars. GitHub's
  owner-visible fourteen-day metrics reported 66 unique clones, and the published
  executable assets have 16 downloads across all releases.

## Repository changes

### Code-signing policy

Add `CODE_SIGNING_POLICY.md` with:

- the SignPath Foundation attribution required for an applicant;
- an explicit `Application pending` status;
- a statement that current releases remain unsigned;
- maintainer, reviewer, and approver roles;
- the intended trusted GitHub Actions and SignPath release flow;
- the rule that only artifacts built from this repository may be signed;
- the rule that signatures must be verified before publication;
- a link to the privacy policy.

After SignPath approves the project and the signing integration is operational,
replace the pending notice with the final attribution: `Free code signing provided
by SignPath.io, certificate by SignPath Foundation.`

### Privacy policy

Add `PRIVACY.md` describing Conduit's real data flow:

- Conduit has no developer-operated cloud service and sends no telemetry to the
  maintainer.
- Clipboard contents, selected files, machine identifiers, network addresses,
  pairing data, and settings are processed only to provide user-requested local
  network operation.
- Peer traffic is authenticated and encrypted, but users remain responsible for
  trusting paired machines and protecting their shared password.
- Local logs and configuration remain on the user's device unless the user chooses
  to share them.
- Uninstall behavior and security-report contact paths are documented.

The policy must avoid promising that sensitive data never leaves a computer,
because Conduit's purpose is to send selected input, clipboard, and file data to
paired computers.

### README disclosure

Add a compact `Code signing and privacy` section near the download and security
information. It will:

- link to the code-signing policy and privacy policy;
- state that the SignPath application is pending;
- state that the current 6.0.1 installer is unsigned;
- direct users to release SHA-256 checksums while signing is pending.

The repository README section will serve as the SignPath application download URL
because it links directly to GitHub Releases and contains the required signing
disclosure.

### Ownership protection

Add `.github/CODEOWNERS` assigning `@parm2006` to:

- `CODE_SIGNING_POLICY.md`;
- `PRIVACY.md`;
- release scripts and installer definitions;
- future workflows and SignPath policy files.

This records responsibility but does not itself enforce review. The repository
owner must enable branch protection separately in GitHub after the files land.

## Work deferred until SignPath approval

Do not add a speculative signing workflow. SignPath supplies the organization ID,
project slug, signing-policy slug, artifact-configuration slug, and trusted-build
configuration after approval. Once those non-secret identifiers exist, implement a
two-stage GitHub-hosted Windows workflow:

1. Build and test the PyInstaller executable.
2. Submit the executable to SignPath and retrieve the signed executable.
3. Package the signed executable with NSIS.
4. Submit the installer to SignPath and retrieve the signed installer.
5. Verify both Authenticode signatures.
6. Generate checksums and provenance after signing.
7. Publish only the verified artifacts after required approval.

The API token will live only in GitHub Actions secrets and must never be committed,
logged, included in an artifact, or shared in chat.

## Application data

Use `Conduit Wireless KVM` as the application project name so a search distinguishes
it from unrelated projects named Conduit. Use the GitHub repository as the homepage
and the README signing-section anchor as the download page. Describe adoption with
the exact public release history and current GitHub metrics; do not claim media
coverage, community discussions, or widespread use that does not exist.

Personal fields such as the applicant's email must be entered by the maintainer.
Leave Company Name blank unless the maintainer represents a registered legal entity.

## Verification

Before presenting the repository preparation as complete:

1. Check Markdown links and headings.
2. Confirm the README download link and signing-policy link resolve within GitHub.
3. Scan the new policies for placeholders, unsupported promises, and conflicting
   statements about approval or signing status.
4. Run the release-packaging documentation tests and the full automated test suite.
5. Run `git diff --check` and inspect the final diff.

No release asset, GitHub release description, branch protection setting, SignPath
account, or external form will be changed without a separate authorized action.
