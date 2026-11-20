"""Pack the RZGit VS Code extension's folder into a .vsix (spec §8, RZG-75).

A .vsix is a zip: ``[Content_Types].xml``, ``extension.vsixmanifest`` and the
extension's files under ``extension/``. ``vsce`` would make one, but needs npm;
this needs nothing, so RZGit can install the extension itself. Plain Python.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

# Only these go into the package (no tests or editor files).
FILES = ("package.json", "extension.js", "README.md")

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".md" ContentType="text/markdown"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""


@dataclass(frozen=True)
class ExtensionInfo:
    publisher: str
    name: str
    version: str
    display_name: str
    description: str
    engine: str  # engines.vscode, e.g. "^1.80.0"

    @property
    def id(self) -> str:
        return f"{self.publisher}.{self.name}"


def extension_info(folder: Path) -> ExtensionInfo:
    """The identity in the folder's ``package.json``."""
    package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    return ExtensionInfo(package["publisher"], package["name"], package["version"],
                         package.get("displayName", package["name"]),
                         package.get("description", ""), package["engines"]["vscode"])


def manifest(info: ExtensionInfo) -> str:
    attr = quoteattr  # a quoted, escaped attribute value
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id={attr(info.name)} Version={attr(info.version)} Publisher={attr(info.publisher)}/>
    <DisplayName>{escape(info.display_name)}</DisplayName>
    <Description xml:space="preserve">{escape(info.description)}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value={attr(info.engine)}/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace"/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def build_vsix(folder: Path, out: Path) -> ExtensionInfo:
    """Write the extension in ``folder`` to ``out`` as a .vsix; its identity."""
    info = extension_info(folder)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as vsix:
        vsix.writestr("[Content_Types].xml", CONTENT_TYPES)
        vsix.writestr("extension.vsixmanifest", manifest(info))
        for name in FILES:
            if (folder / name).is_file():
                vsix.write(folder / name, f"extension/{name}")
    return info
