# Changelog

## [7.3.1] - 2026-11-07

### Fixed

- Made installed Conduit shortcuts searchable in Windows with a dedicated launcher, whether automatic sign-in startup is enabled or disabled.

- Fixed first-time installation failing to create the approved launch task.
- Kept native video resize and cleanup off the GUI thread to prevent a renderer deadlock from blocking background-mode recovery and exit.
- Conduit now runs elevated to control apps such as Windhawk; direct executable and portable launches request administrator approval.

### Added

- Approve installed Conduit once during setup, then launch it from the desktop or Start menu without repeated administrator prompts.
- Choose automatic launch at sign-in separately for each computer in the installer.
