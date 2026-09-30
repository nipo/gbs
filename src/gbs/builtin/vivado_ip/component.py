"""Identity of a packaged Vivado IP

A packaged IP describes itself in a `component.xml` IP-XACT file. Vivado
instantiates it by VLNV (vendor, library, name, version), which is read
from there rather than from the backend configuration: the package is
the thing under check, not what the project believes it contains.
"""

from __future__ import annotations
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ...build.task import BuildError

__all__ = ["IpComponent"]


@dataclass(frozen=True)
class IpComponent:
    """VLNV of a packaged IP"""

    vendor: str
    library: str
    name: str
    version: str

    FILE_NAME = "component.xml"

    # Vivado writes SPIRIT 1685-2009; later IP-XACT revisions keep the
    # same component identity elements.
    NAMESPACES = (
        "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009",
        "http://www.accellera.org/XMLSchema/IPXACT/1685-2014",
        "http://www.accellera.org/XMLSchema/IPXACT/1685-2022",
    )

    FIELDS = ("vendor", "library", "name", "version")

    @property
    def vlnv(self) -> str:
        return f"{self.vendor}:{self.library}:{self.name}:{self.version}"

    @classmethod
    def parse(cls, data: bytes, origin: str) -> IpComponent:
        """Read the identity of a component.xml document

        Args:
            data: Document contents
            origin: Where the document comes from, for error messages
        """
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise BuildError(f"{origin}: not a valid XML document: {e}")

        namespace, tag = "", root.tag
        if root.tag.startswith("{"):
            namespace, _, tag = root.tag[1:].partition("}")
        if namespace not in cls.NAMESPACES:
            raise BuildError(
                f"{origin}: root element {root.tag} is not an IP-XACT "
                f"component (expected one of namespaces "
                f"{', '.join(cls.NAMESPACES)})")
        if tag != "component":
            raise BuildError(
                f"{origin}: root element is {tag}, expected component")

        values = {}
        for field in cls.FIELDS:
            element = root.find(f"{{{namespace}}}{field}")
            text = (element.text or "").strip() if element is not None else ""
            if not text:
                raise BuildError(f"{origin}: component has no {field}")
            values[field] = text

        return cls(**values)

    @classmethod
    def from_zip(cls, path: Path) -> IpComponent:
        """Read the identity of a packaged IP zip"""
        try:
            with zipfile.ZipFile(path) as zf:
                names = [n for n in zf.namelist()
                         if n.rsplit("/", 1)[-1] == cls.FILE_NAME]
                if len(names) != 1:
                    raise BuildError(cls.count_error(path, names))
                data = zf.read(names[0])
        except (OSError, zipfile.BadZipFile) as e:
            raise BuildError(f"{path}: cannot read IP zip: {e}")

        return cls.parse(data, f"{path}:{names[0]}")

    @classmethod
    def from_dir(cls, path: Path) -> IpComponent:
        """Read the identity of a packaged IP directory"""
        if not path.is_dir():
            raise BuildError(f"{path}: not a directory")

        found = sorted(path.rglob(cls.FILE_NAME))
        if len(found) != 1:
            raise BuildError(cls.count_error(path, found))

        return cls.parse(found[0].read_bytes(), str(found[0]))

    @classmethod
    def load(cls, resource) -> IpComponent:
        """Read the identity of a packaged IP resource"""
        if resource.file_type == "vivado-ip-zip":
            return cls.from_zip(resource.path)
        if resource.file_type == "vivado-ip-dir":
            return cls.from_dir(resource.path)
        raise AssertionError(
            f"{resource.path}: {resource.file_type} is not a packaged IP")

    @classmethod
    def count_error(cls, path: Path, found: list) -> str:
        if not found:
            return f"{path}: no {cls.FILE_NAME} in packaged IP"
        return (f"{path}: packaged IP holds several {cls.FILE_NAME}: "
                + ", ".join(str(f) for f in found))
