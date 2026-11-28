"""Build the Python launcher fetched by AMP Update, reproducibly from reviewed sources."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def build(dest):
    with zipfile.ZipFile(dest, "w") as archive:
        for source, name in (("service.py", "__main__.py"), ("panel_addons.py", "panel_addons.py")):
            archive.writestr(zipfile.ZipInfo(name), (ROOT / source).read_bytes())


if __name__ == "__main__":
    build(ROOT / "updates/se2-panel-v4.pyz")
