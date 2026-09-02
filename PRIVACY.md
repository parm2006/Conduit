# Privacy Policy

Last updated: August 29, 2026

## Scope

This policy applies to the official Conduit Windows application and release artifacts published by the Conduit project.

## No maintainer-operated data collection

Conduit does not require an account and does not include project-operated analytics, advertising, crash-reporting, or telemetry services. The Conduit maintainer does not automatically receive your usage data, clipboard contents, transferred files, passwords, or diagnostic information.

## Data processed by Conduit

To provide its local-network KVM and sharing features, Conduit processes:

- Keyboard and mouse input needed to control a connected machine.
- The newest supported clipboard item, including text, images, HTML, or RTF, when clipboard sharing is enabled.
- Files or folders selected for the on-paste file relay.
- Machine identifiers, Windows machine names, display geometry, topology, IP addresses, ports, and connection state needed for discovery, connection, and screen arrangement.
- Pairing information, certificate fingerprints, shared passwords, and cryptographic material needed to authenticate peers and protect connections.
- Application preferences, trusted-peer identity, transfer state, and operational diagnostics needed to run and troubleshoot the application.

Clipboard contents, input events, and selected transfer content are exchanged only among Conduit machines you connect on the network. Conduit does not upload them to a service operated by the project maintainer.

## Network and security

Conduit communicates directly between participating machines over the local network. It uses authenticated pairing and TLS identity checks to protect peer connections. You are responsible for using a strong shared password, approving only pairing requests you recognize, and securing every connected machine. No software or network control can guarantee absolute security.

## Local storage and retention

Conduit stores application state under `%LOCALAPPDATA%\Conduit`, including preferences, identity and trust data, and transfer-related state. Preferences can include recent server addresses, ports, machine layout, and display information. Private identity keys are protected with Windows Data Protection API (DPAPI) for the current Windows user.

Conduit keeps local state until it is replaced or deleted. Uninstalling the application may leave this per-user data in place. After closing Conduit, you may delete `%LOCALAPPDATA%\Conduit` to remove it, but doing so also removes saved preferences, trust relationships, identity material, and any remaining transfer state.

## Diagnostics and reports

Operational logs or screenshots may contain network addresses, machine names, local file paths, connection metadata, or content visible in the application. Conduit does not transmit diagnostics automatically. Before sharing a report, remove sensitive information and follow the private reporting guidance in [SECURITY.md](SECURITY.md).

## Third-party services

The Conduit application does not need a project-operated cloud service. Downloading Conduit or interacting with its source, issues, and releases through GitHub is governed by GitHub's own privacy practices.

## Changes and contact

Material changes to this policy will be published in the repository history. For privacy or security questions, contact the maintainer through the methods in [SECURITY.md](SECURITY.md).
