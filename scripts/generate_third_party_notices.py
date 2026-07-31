"""Generate auditable third-party notices from installed metadata."""

import argparse
from importlib import metadata
from pathlib import Path
import platform
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.version import (
    NSIS_LICENSE_URL,
    NSIS_SOURCE_URL,
    PYINSTALLER_LICENSE_URL,
    PYINSTALLER_SOURCE_URL,
    SOURCE_URL,
)


RELEASE_DISTRIBUTIONS = (
    "altgraph",
    "cffi",
    "cryptography",
    "customtkinter",
    "darkdetect",
    "packaging",
    "pefile",
    "PyInstaller",
    "pyinstaller-hooks-contrib",
    "pynput",
    "pywin32",
    "six",
)


def _source_url(distribution):
    project_urls = distribution.metadata.get_all("Project-URL") or []
    preferred = (
        "source",
        "source code",
        "repository",
        "homepage",
        "download",
    )
    parsed = []
    for item in project_urls:
        label, separator, url = item.partition(",")
        if separator and url.strip().startswith(("https://", "http://")):
            parsed.append((label.strip().casefold(), url.strip()))
    for label in preferred:
        for candidate_label, url in parsed:
            if candidate_label == label:
                return url
    homepage = distribution.metadata.get("Home-page")
    if homepage and str(homepage).startswith(("https://", "http://")):
        return str(homepage)
    return None


def _license_files(distribution):
    matches = []
    for relative in distribution.files or ():
        parts = tuple(part.casefold() for part in Path(relative).parts)
        name = Path(relative).name.casefold()
        in_metadata = any(part.endswith(".dist-info") for part in parts)
        is_notice = name.startswith(("license", "copying", "notice"))
        if in_metadata and is_notice:
            matches.append(relative)
    return sorted(matches, key=lambda value: str(value).casefold())


def _distribution_notice(distribution):
    name = distribution.metadata.get("Name")
    source = _source_url(distribution)
    license_files = _license_files(distribution)
    if not name or not source or not license_files:
        raise ValueError(
            "distribution metadata is missing a name, source, or license"
        )

    license_name = (
        distribution.metadata.get("License-Expression")
        or distribution.metadata.get("License")
        or "See included license text"
    )
    texts = []
    for relative in license_files:
        path = Path(distribution.locate_file(relative))
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise ValueError("distribution license text is unreadable") from error
        if not text:
            raise ValueError("distribution license text is empty")
        texts.append(text)

    sections = [
        f"{name} {distribution.version}",
        f"Declared license: {license_name}",
        f"Source: {source}",
        "",
    ]
    sections.extend(texts)
    return "\n".join(sections)


def _runtime_notices():
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    tk_licenses = sorted(
        (Path(sys.base_prefix) / "tcl").glob("tk*/license.terms")
    )
    if not python_license.is_file() or not tk_licenses:
        raise ValueError("Python or Tcl/Tk runtime license text is missing")
    try:
        python_text = python_license.read_text(encoding="utf-8").strip()
        tk_text = tk_licenses[0].read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ValueError("runtime license text is unreadable") from error
    if not python_text or not tk_text:
        raise ValueError("runtime license text is empty")
    return [
        f"Python {platform.python_version()} runtime",
        "Source: https://www.python.org/downloads/source/",
        "License: https://docs.python.org/3/license.html",
        "",
        python_text,
        "",
        "-" * 72,
        "",
        "Tcl/Tk runtime",
        "Source: https://www.tcl-lang.org/software/tcltk/",
        "License: installed Tcl/Tk license.terms",
        "",
        tk_text,
    ]


def generate_notices(
    distribution_names=RELEASE_DISTRIBUTIONS,
    *,
    distribution_factory=metadata.distribution,
):
    sections = [
        "DeskFlow Third-Party Notices",
        "============================",
        "",
        "DeskFlow is licensed under GPL-3.0.",
        f"Corresponding source: {SOURCE_URL}",
        "",
        "Release tooling",
        "---------------",
        "PyInstaller (GPL with bootloader exception)",
        f"Source: {PYINSTALLER_SOURCE_URL}",
        f"License: {PYINSTALLER_LICENSE_URL}",
        "",
        "NSIS (zlib/libpng license; installer generator and stub)",
        f"Source: {NSIS_SOURCE_URL}",
        f"License: {NSIS_LICENSE_URL}",
        "",
        "-" * 72,
        "",
    ]
    sections.extend(_runtime_notices())
    for name in sorted(distribution_names, key=str.casefold):
        distribution = distribution_factory(name)
        sections.extend(("", "-" * 72, "", _distribution_notice(distribution)))
    return "\n".join(sections).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generate_notices(), encoding="utf-8")
    if output.stat().st_size == 0:
        raise RuntimeError("third-party notice output is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
