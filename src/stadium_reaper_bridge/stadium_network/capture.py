"""Optional packet-capture import boundary.

The initial lab recognizes standard files but deliberately avoids a mandatory
parser dependency.  A future adapter can implement ``CaptureImporter``.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class CaptureImporter(ABC):
    @abstractmethod
    def import_file(self, path: Path):
        """Return neutral ProtocolObservation instances."""


def recognize_capture(path: Path) -> str:
    data = path.read_bytes()[:4]
    if data == b"\x0a\x0d\x0d\x0a":
        return "pcapng"
    if data in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        return "pcap"
    raise ValueError("not a recognized pcap or pcapng file")
