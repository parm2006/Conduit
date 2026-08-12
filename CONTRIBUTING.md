# Contributing to Conduit

Thank you for helping improve Conduit. Bug reports, feature ideas,
documentation fixes, tests, and code contributions are welcome.

By submitting a contribution, you agree that it may be distributed under the
project's [GPL-3.0 license](LICENSE).

## Before you start

- Search the [existing issues](https://github.com/parm2006/Conduit/issues) to
  avoid duplicates.
- Use the bug report form for defects and the feature request form for new
  ideas.
- Do not post suspected vulnerabilities, credentials, pairing information,
  certificates, private keys, or sensitive logs in a public issue. Follow the
  [security policy](SECURITY.md) instead.
- For a substantial change, open an issue before writing code so the approach
  can be discussed.

## Report a bug

A useful bug report includes:

- The Conduit version and how it was installed.
- The Windows version and Conduit role (server or client) for both PCs.
- Clear steps that reproduce the problem.
- What you expected and what happened instead.
- Sanitized logs, screenshots, or recordings when they help explain the issue.

Remove IP addresses, passwords, pairing data, certificates, keys, usernames,
file paths, and clipboard contents before attaching diagnostic information.

## Suggest a feature

Describe the problem you want to solve before describing a solution. Include
the intended workflow, who benefits, and any alternatives you considered.
Focused requests are easier to evaluate and implement.

## Develop locally

Conduit development requires Windows 10 or 11 and Python. From PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run.py
```

Keep changes focused. Do not commit virtual environments, build outputs,
generated installers, logs, local certificates, keys, or machine-specific
configuration.

## Test changes

Run the complete automated suite and whitespace check before opening a pull
request:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Add or update tests for behavior changes. For user-facing changes, explain how
you tested the workflow manually on the relevant Windows machines.

## Open a pull request

1. Fork the repository and create a branch from the default branch.
2. Make one focused change with clear commit messages.
3. Update tests and documentation when behavior changes.
4. Complete the pull request template and link the related issue.
5. Respond to review feedback with follow-up commits.

Maintainers may ask to narrow a pull request, add tests, revise documentation,
or discuss a different approach. Opening a pull request does not guarantee
that it will be merged.

## Community standards

Be respectful and constructive. All project spaces and interactions follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
