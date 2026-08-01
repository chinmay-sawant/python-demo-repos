"""Unit tests for the XMP metadata packet builder (engine.meta).

Verifies the packet framing (xpacket begin PI with BOM and the fixed
Adobe packet ID, ``end="w"`` closing PI, >= 2048 bytes of padding), the
PDF/A-4 identification (``pdfaid:part 4`` / ``pdfaid:rev 2020``), the
required namespaces, ISO 8601 dates, ``dc:format``, producer / creator
tool and the deterministic ``uuid:...`` document IDs, and that optional
``dc:title`` / ``dc:creator`` / ``dc:subject`` / ``dc:description`` are
only emitted when provided.
"""

from __future__ import annotations

import datetime
import unittest

from engine.meta import XMP_PACKET_ID, build_xmp_dict, build_xmp_packet

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)
FIXED_PRODUCER = "pythoncoreengine 0.1.0"

_XPACKET_BEGIN = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'


def _packet(**overrides: object) -> bytes:
    kwargs: dict = {"created": FIXED_CREATED, "producer": FIXED_PRODUCER}
    kwargs.update(overrides)
    return build_xmp_packet(**kwargs)


class TestPacketFraming(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = _packet()

    def test_begins_with_xpacket_header_and_bom(self) -> None:
        self.assertTrue(self.packet.startswith(_XPACKET_BEGIN), self.packet[:40])

    def test_ends_with_xpacket_end_w(self) -> None:
        self.assertTrue(self.packet.rstrip().endswith(b'<?xpacket end="w"?>'))

    def test_packet_id_is_adobe_fixed_id(self) -> None:
        self.assertEqual(XMP_PACKET_ID, "W5M0MpCehiHzreSzNTczkc9d")
        self.assertIn(XMP_PACKET_ID.encode("ascii"), self.packet)

    def test_packet_is_padded_to_at_least_2048_bytes(self) -> None:
        self.assertGreaterEqual(len(self.packet), 2048)
        self.assertEqual(len(self.packet) % 4, 0)

    def test_xmpmeta_root_and_rdf(self) -> None:
        self.assertIn(b'<x:xmpmeta xmlns:x="adobe:ns:meta/"', self.packet)
        self.assertIn(
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
            self.packet,
        )
        self.assertIn(b"<rdf:Description rdf:about=\"\">", self.packet)
        self.assertIn(b"</x:xmpmeta>", self.packet)


class TestPDFAIdentification(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = _packet()

    def test_pdfaid_part_4_rev_2020(self) -> None:
        self.assertIn(b"<pdfaid:part>4</pdfaid:part>", self.packet)
        self.assertIn(b"<pdfaid:rev>2020</pdfaid:rev>", self.packet)

    def test_pdfaid_namespace(self) -> None:
        self.assertIn(b'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"', self.packet)

    def test_all_schema_namespaces_present(self) -> None:
        for namespace in (
            b'xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
            b'xmlns:dc="http://purl.org/dc/elements/1.1/"',
            b'xmlns:pdf="http://ns.adobe.com/pdf/1.3/"',
            b'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"',
        ):
            self.assertIn(namespace, self.packet)


class TestProperties(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = _packet()

    def test_dates_are_iso8601(self) -> None:
        self.assertIn(b"<xmp:CreateDate>2026-08-01T12:00:00Z</xmp:CreateDate>", self.packet)
        self.assertIn(b"<xmp:ModifyDate>2026-08-01T12:00:00Z</xmp:ModifyDate>", self.packet)
        self.assertIn(b"<xmp:MetadataDate>2026-08-01T12:00:00Z</xmp:MetadataDate>", self.packet)

    def test_producer_and_creator_tool(self) -> None:
        self.assertIn(b"<pdf:Producer>pythoncoreengine 0.1.0</pdf:Producer>", self.packet)
        self.assertIn(b"<xmp:CreatorTool>pythoncoreengine</xmp:CreatorTool>", self.packet)

    def test_dc_format(self) -> None:
        self.assertIn(b"<dc:format>application/pdf</dc:format>", self.packet)

    def test_document_and_instance_ids_are_uuid(self) -> None:
        self.assertRegex(
            self.packet.decode("utf-8"),
            r"<xmpMM:DocumentID>uuid:[0-9a-f-]{36}</xmpMM:DocumentID>",
        )
        self.assertRegex(
            self.packet.decode("utf-8"),
            r"<xmpMM:InstanceID>uuid:[0-9a-f-]{36}</xmpMM:InstanceID>",
        )

    def test_optional_dc_fields_omitted_by_default(self) -> None:
        for field in (b"dc:title", b"dc:creator", b"dc:subject", b"dc:description"):
            self.assertNotIn(b"<" + field, self.packet)

    def test_optional_dc_fields_emitted_when_given(self) -> None:
        packet = _packet(
            title="Report",
            creator="Test Author",
            subject="Testing",
            description="A description",
        )
        # dc:title is emitted as a language-alternative array with an
        # x-default item (the form UA-2 validators read back).
        self.assertIn(b'<dc:title><rdf:Alt>', packet)
        self.assertIn(b'<rdf:li xml:lang="x-default">Report</rdf:li>', packet)
        self.assertIn(b"</rdf:Alt></dc:title>", packet)
        self.assertIn(b"<dc:creator>Test Author</dc:creator>", packet)
        self.assertIn(b"<dc:subject>Testing</dc:subject>", packet)
        self.assertIn(b"<dc:description>A description</dc:description>", packet)


class TestDeterminism(unittest.TestCase):
    def test_identical_inputs_produce_identical_packets(self) -> None:
        first = build_xmp_packet(created=FIXED_CREATED, producer=FIXED_PRODUCER)
        second = build_xmp_packet(created=FIXED_CREATED, producer=FIXED_PRODUCER)
        self.assertEqual(first, second)

    def test_different_dates_change_dates_and_ids(self) -> None:
        earlier = build_xmp_packet(
            created=datetime.datetime(2020, 1, 2, 3, 4, 5), producer=FIXED_PRODUCER
        )
        later = build_xmp_packet(created=FIXED_CREATED, producer=FIXED_PRODUCER)
        self.assertNotEqual(earlier, later)


class TestXMPDict(unittest.TestCase):
    def test_build_xmp_dict_values(self) -> None:
        values = build_xmp_dict(created=FIXED_CREATED, producer=FIXED_PRODUCER)
        self.assertEqual(values["pdfaid:part"], "4")
        self.assertEqual(values["pdfaid:rev"], "2020")
        self.assertEqual(values["dc:format"], "application/pdf")
        self.assertEqual(values["xmp:CreateDate"], "2026-08-01T12:00:00Z")
        self.assertTrue(values["xmpMM:DocumentID"].startswith("uuid:"))
        self.assertTrue(values["xmpMM:InstanceID"].startswith("uuid:"))


if __name__ == "__main__":
    unittest.main()
