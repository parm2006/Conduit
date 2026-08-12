# Conduit Full Rename Design

## Goal

Rename the product and the complete first-party working tree from DeskFlow to
Conduit. The result must contain no current-tree occurrence of `DeskFlow`,
`deskflow`, or `DESKFLOW`, and no first-party filename or directory may retain
the old name.

This is a clean break. Conduit will not discover, migrate, update, uninstall,
or delete an existing DeskFlow installation or its local data. No users depend
on compatibility with the old name.

## Rename rules

Apply these mappings according to the case of each existing token:

| Existing form | New form |
| --- | --- |
| `DeskFlow` | `Conduit` |
| `deskflow` | `conduit` |
| `DESKFLOW` | `CONDUIT` |

The mapping applies to prose, code identifiers, string constants, filenames,
paths, URLs, command-line flags, protocol event names, comments, test names,
test fixtures, logs, installer metadata, and generated artifact names.

## Product and runtime identity

Conduit becomes a new Windows application identity:

- Product name: `Conduit`
- Executable: `Conduit.exe`, with versioned release filenames derived from it
- Installer and install directory: `Conduit` and `C:\Program Files\Conduit`
- Local application data: `%LOCALAPPDATA%\Conduit`
- Registry and uninstall keys: `Conduit`
- Start Menu and desktop shortcuts: `Conduit`
- Firewall rule and group: `Conduit`
- TLS certificate subject and alternative name: `Conduit`
- Internal command-line flag: `--conduit-firewall-helper`
- Internal protocol heartbeat names: `__conduit_heartbeat__` and
  `__conduit_heartbeat_ack__`
- Source and community URLs: `https://github.com/parm2006/Conduit`

No old-name aliases or fallback paths will remain.

## Source and packaging changes

Rename public and internal Python identifiers, including GUI, client, server,
firewall, cancellation, and helper symbols. Update every import and use in the
application and test suite.

Rename `DeskFlow.spec` to `Conduit.spec` and `installer/DeskFlow.nsi` to
`installer/Conduit.nsi`. Update the release PowerShell script, PyInstaller
metadata, NSIS definitions, installer safety checks, release inventory, and
packaging tests so every input and output uses Conduit.

Update all first-party documentation and operational records, including the
README, release notes, contribution files, issue and pull request templates,
security policy, ignored design documents, handoffs, and local agent artifacts.
Filenames containing the old name must also change.

This design spec necessarily records the old tokens and old workspace path. It
is a temporary implementation record and will be deleted from the working tree
as the final content-cleanup step. Its committed historical copy remains within
the Git-history exclusion described below.

The application icon contains no known textual product name and may remain if
a binary scan and visual inspection show no old branding.

## Generated and local artifacts

Old build and distribution outputs can preserve embedded product strings. Delete
the repository's generated `build` and `dist` contents after resolving their
absolute paths inside the project, then regenerate the third-party notices and
run the supported release build when the required local NSIS tool is available.
The virtual environment is third-party/local tooling and is excluded from the
brand scan, but no project dependency or source file may be added there.

Python bytecode caches are generated artifacts. Remove them or regenerate them
only after the rename so stale bytecode cannot retain old identifiers.

## Workspace and repository name

Perform the root folder rename last, after all commands that depend on the
current workspace path have finished. Rename
`C:\Users\parth\Projects\DeskFlow` to
`C:\Users\parth\Projects\Conduit`. Verify Git from the new location.

The user will rename the GitHub repository. First-party files will point to the
future `parm2006/Conduit` URL. The local Git remote may be updated after the
GitHub rename; it must not block the source rename.

## Verification

Verification must establish all of the following:

1. A case-insensitive recursive text and filename scan finds no old product
   name in the first-party working tree.
2. The scan includes tracked files, ignored project documentation, handoffs,
   `.agents`, `.codex`, and other first-party hidden files. It excludes only
   `.git`, `venv`, and third-party caches. Git history remains unchanged.
3. Python compilation succeeds from the renamed source tree.
4. The complete unit test suite passes with renamed test contracts.
5. `git diff --check` reports no whitespace errors.
6. Git records the spec and installer renames as renames or equivalent
   delete/add changes with correct contents.
7. Third-party notices generate successfully under the Conduit name.
8. The release build succeeds if NSIS is installed. If it is unavailable, all
   pre-NSIS build gates and packaging contract tests must pass, and the missing
   external tool must be reported explicitly.
9. The final project directory is named `Conduit`, and Git status works from
   that directory.

Binary Git objects and historical commits are outside the rename boundary.
Rewriting them would alter published history and is neither required nor
authorized.

## Safety and rollback

Preserve the user's current uncommitted community files and release notes as
part of the rename. Do not reset or discard unrelated work.

Before deleting generated outputs, resolve and verify that each target is a
child of the project root. Before renaming the root folder, verify that the
destination does not exist. If Windows holds a file open or the application
still references the old workspace, stop and report the exact blocker rather
than merging directories or overwriting content.
