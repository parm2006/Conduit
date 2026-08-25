# Conduit v5.1.1

Conduit v5.1.1 replaces v5.1. The earlier v5.1 installer was published from a
release whose automatic source archive pointed to the older pre-rename tree.
Use v5.1.1 so the installer and corresponding source refer to the same commit.

## License and source

- License: GPL-3.0-only
- Exact corresponding source: https://github.com/parm2006/Conduit/tree/v5.1.1
- Third-party licenses: attached `THIRD_PARTY_NOTICES.txt`
- Artifact hashes: attached `SHA256SUMS.txt`
- Build and dependency provenance: attached `RELEASE_MANIFEST.txt`

## Changes since v5.1

- Completes the DeskFlow-to-Conduit rename in the published source.
- Replaces the inherited CustomTkinter icon with original Conduit artwork.
- Pins the complete Python release dependency graph.
- Adds explicit Roboto, CustomTkinter, and NSIS LZMA licensing information.
- Signs the inner executable before installer assembly when signing is enabled.
- Requires clean, exactly tagged source for public release builds.
- Produces an explicit corresponding-source archive and SHA-256 checksums.

Install `Conduit-v5.1.1-Setup.exe` on both Windows PCs. See the README for
setup and firewall details.
