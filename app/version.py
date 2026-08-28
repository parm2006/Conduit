"""Canonical Conduit release metadata."""

PRODUCT_NAME = "Conduit"
PRODUCT_VERSION = "6.0.1"
FILE_VERSION = (6, 0, 1, 0)
FILE_VERSION_STRING = ".".join(str(part) for part in FILE_VERSION)
SOURCE_URL = "https://github.com/parm2006/Conduit"
RELEASE_SOURCE_URL = f"{SOURCE_URL}/tree/v{PRODUCT_VERSION}"

PYINSTALLER_SOURCE_URL = "https://github.com/pyinstaller/pyinstaller/tree/v6.22.0"
PYINSTALLER_LICENSE_URL = (
    "https://pyinstaller.org/en/stable/license.html"
)
NSIS_SOURCE_URL = "https://github.com/kichik/nsis/tree/v3.12"
NSIS_LICENSE_URL = "https://nsis.sourceforge.io/License"
