"""Runtime data lives outside the source checkout by default."""
import os
import sys
from pathlib import Path


def application_home():
    if os.getenv("AFTERHOURS_HOME"):
        return Path(os.environ["AFTERHOURS_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Afterhours"
    return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "afterhours"


def data_directory():
    return Path(os.getenv("DATA_DIR", str(application_home() / "state"))).expanduser()


def brain_directory():
    return Path(os.getenv("BRAIN_DIR", str(application_home() / "brain"))).expanduser()
