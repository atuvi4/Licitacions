"""Canal ZIP: obrir un paquet de licitacio sense confiar-hi (capa de recepcio).

Dues respostes diferents i que no s'han de barrejar:

* **paquet hostil o trencat** → es rebutja sencer (`ArchiveRejected`);
* **un fitxer que no es pot fer servir** → s'omet amb motiu i la resta continua.

Tot en memoria: aquests tests no toquen el disc ni cap base de dades.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.models.enums import ReceptionItemStatus, ReceptionSkipReason
from app.reception.archive import ArchiveLimits, ArchiveRejected, scan_archive
from tests.fixtures.pdf_builder import build_pdf

LIMITS = ArchiveLimits(
    max_members=20,
    max_member_bytes=5 * 1024 * 1024,
    max_total_bytes=20 * 1024 * 1024,
    max_ratio=200,
)


def make_zip(entries, *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    """`entries`: llista de (nom | ZipInfo, bytes)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def _statuses(scan):
    return [(e.path, e.status, e.skip_reason) for e in scan.entries]


def _rejected(data: bytes, limits: ArchiveLimits = LIMITS) -> str:
    with pytest.raises(ArchiveRejected) as exc:
        scan_archive(data, limits)
    return exc.value.reason


# --------------------------------------------------------------------------- #
# El cami normal                                                               #
# --------------------------------------------------------------------------- #


def test_reads_every_file_in_directory_order_with_folders():
    pdf = build_pdf(["PCAP"])
    data = make_zip(
        [
            ("PCAP.pdf", pdf),
            ("Resta Documentacio/Informe.pdf", pdf + b" "),
            ("Resta Documentacio/", b""),
        ]
    )
    scan = scan_archive(data, LIMITS)
    assert [(e.position, e.path, e.filename) for e in scan.entries] == [
        (0, "PCAP.pdf", "PCAP.pdf"),
        (1, "Resta Documentacio/Informe.pdf", "Informe.pdf"),
    ]
    assert all(e.status is ReceptionItemStatus.ACCEPTED for e in scan.entries)
    assert scan.entries[0].data == pdf


def test_system_files_and_empty_files_are_listed_as_skipped_not_fatal():
    data = make_zip(
        [
            ("__MACOSX/._PCAP.pdf", b"x"),
            (".DS_Store", b"x"),
            ("Thumbs.db", b"x"),
            ("~$PPT.docx", b"x"),
            ("buit.pdf", b""),
            ("PCAP.pdf", build_pdf(["PCAP"])),
        ]
    )
    scan = scan_archive(data, LIMITS)
    assert _statuses(scan) == [
        ("__MACOSX/._PCAP.pdf", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.SYSTEM_FILE),
        (".DS_Store", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.SYSTEM_FILE),
        ("Thumbs.db", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.SYSTEM_FILE),
        ("~$PPT.docx", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.SYSTEM_FILE),
        ("buit.pdf", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.EMPTY),
        ("PCAP.pdf", ReceptionItemStatus.ACCEPTED, None),
    ]
    assert all(e.data is None for e in scan.entries[:5])


def test_a_file_over_the_member_limit_is_skipped_without_reading_it():
    limits = ArchiveLimits(
        max_members=20, max_member_bytes=100, max_total_bytes=10_000, max_ratio=200
    )
    data = make_zip(
        [("gran.pdf", b"%PDF-" + b"a" * 500), ("petit.pdf", b"%PDF-1")],
        compression=zipfile.ZIP_STORED,
    )
    scan = scan_archive(data, limits)
    assert _statuses(scan) == [
        ("gran.pdf", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.TOO_LARGE),
        ("petit.pdf", ReceptionItemStatus.ACCEPTED, None),
    ]
    assert scan.entries[0].data is None


def test_office_documents_inside_are_documents_not_nested_archives():
    docx = make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w/>")])
    scan = scan_archive(make_zip([("Acord.docx", docx)]), LIMITS)
    assert _statuses(scan) == [("Acord.docx", ReceptionItemStatus.ACCEPTED, None)]
    assert scan.entries[0].data == docx


def test_a_nested_zip_is_opened_one_level_and_its_files_carry_its_path():
    pdf = build_pdf(["Annex"])
    inner = make_zip([("Annex 1.pdf", pdf)])
    scan = scan_archive(make_zip([("annexos.zip", inner), ("PCAP.pdf", pdf + b" ")]), LIMITS)
    assert _statuses(scan) == [
        ("annexos.zip", ReceptionItemStatus.EXPANDED, None),
        ("annexos.zip/Annex 1.pdf", ReceptionItemStatus.ACCEPTED, None),
        ("PCAP.pdf", ReceptionItemStatus.ACCEPTED, None),
    ]
    assert scan.entries[1].depth == 1


def test_a_zip_inside_a_nested_zip_is_not_opened():
    deepest = make_zip([("x.pdf", build_pdf(["X"]))])
    inner = make_zip([("mes.zip", deepest)])
    scan = scan_archive(make_zip([("annexos.zip", inner)]), LIMITS)
    assert _statuses(scan) == [
        ("annexos.zip", ReceptionItemStatus.EXPANDED, None),
        ("annexos.zip/mes.zip", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.NESTED_TOO_DEEP),
    ]


def test_a_corrupt_nested_zip_is_skipped_not_fatal():
    broken = b"PK\x03\x04" + b"\x00" * 40
    scan = scan_archive(make_zip([("trencat.zip", broken)]), LIMITS)
    assert _statuses(scan) == [
        ("trencat.zip", ReceptionItemStatus.SKIPPED, ReceptionSkipReason.UNREADABLE_ARCHIVE)
    ]


# --------------------------------------------------------------------------- #
# Noms                                                                         #
# --------------------------------------------------------------------------- #


def test_utf8_names_without_the_utf8_flag_are_recovered():
    """Moltes eines de Windows escriuen UTF-8 sense marcar-ho: zipfile ho llegiria
    com a CP437 ("Documentaci├│")."""
    placeholder = "Resta Documentacioo/PCAP.pdf"
    real = "Resta Documentació/PCAP.pdf"
    assert len(placeholder.encode()) == len(real.encode("utf-8"))
    data = make_zip([(placeholder, build_pdf(["PCAP"]))])
    data = data.replace(placeholder.encode(), real.encode("utf-8"))

    scan = scan_archive(data, LIMITS)
    assert scan.entries[0].path == real


def test_legacy_cp437_names_keep_their_standard_reading():
    info = zipfile.ZipInfo("Informe necessitat.pdf")
    data = make_zip([(info, build_pdf(["Informe"]))])
    data = data.replace(b"Informe necessitat", b"Informe necessit\xa2t")  # 0xA2 = o accentuada
    scan = scan_archive(data, LIMITS)
    assert scan.entries[0].path == "Informe necessitót.pdf"


# --------------------------------------------------------------------------- #
# Es rebutja sencer                                                            #
# --------------------------------------------------------------------------- #


def test_not_a_zip_is_rejected():
    assert _rejected(build_pdf(["PCAP"])) == "not_a_zip"


def test_a_truncated_zip_is_rejected_as_corrupt():
    data = make_zip([("PCAP.pdf", build_pdf(["PCAP"]))])
    assert _rejected(data[: len(data) // 2]) == "corrupt"


def test_an_office_document_is_not_a_package():
    docx = make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w/>")])
    assert _rejected(docx) == "office_document"


@pytest.mark.parametrize(
    "name",
    [
        "../fora.pdf",
        "annexos/../../fora.pdf",
        "/etc/fora.pdf",
        "C:/Windows/fora.pdf",
        "..\\fora.pdf",
        # El \x00 no: `zipfile` ja talla el nom al primer nul en llegir-lo.
        "annex\x07.pdf",
    ],
)
def test_paths_that_leave_the_package_reject_it(name):
    info = zipfile.ZipInfo("placeholder-name-that-is-long-enough.pdf")
    data = make_zip([(info, b"%PDF-1")])
    raw = name.encode("utf-8").ljust(len(info.filename), b"_")
    if len(raw) > len(info.filename):
        pytest.skip("nom massa llarg per al placeholder")
    patched = data.replace(info.filename.encode(), raw)
    assert _rejected(patched) == "unsafe_path"


def test_a_symlink_rejects_the_package():
    info = zipfile.ZipInfo("enllac.pdf")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    assert _rejected(make_zip([(info, b"/etc/passwd")])) == "symlink"


def test_an_encrypted_member_rejects_the_package():
    # `zipfile` no sap xifrar: es marca el bit 0 del flag a les dues capcaleres.
    data = bytearray(make_zip([("secret.pdf", b"%PDF-1")], compression=zipfile.ZIP_STORED))
    data[6] |= 0x1
    central = data.find(b"PK\x01\x02")
    data[central + 8] |= 0x1
    assert _rejected(bytes(data)) == "encrypted"


def test_two_entries_with_the_same_name_reject_the_package():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("PCAP.pdf", b"%PDF-1")
        with pytest.warns(UserWarning):
            zf.writestr("PCAP.pdf", b"%PDF-2")
    assert _rejected(buffer.getvalue()) == "duplicate_entry"


def test_a_compression_bomb_is_rejected_before_decompressing():
    bomb = make_zip([("bomba.pdf", b"\x00" * (8 * 1024 * 1024))])
    limits = ArchiveLimits(
        max_members=20,
        max_member_bytes=64 * 1024 * 1024,
        max_total_bytes=64 * 1024 * 1024,
        max_ratio=200,
    )
    assert _rejected(bomb, limits) == "compression_ratio"


def test_too_many_files_reject_the_package():
    limits = ArchiveLimits(
        max_members=3, max_member_bytes=1000, max_total_bytes=10_000, max_ratio=200
    )
    data = make_zip([(f"f{i}.pdf", b"%PDF-1") for i in range(4)])
    assert _rejected(data, limits) == "too_many_members"


def test_files_inside_a_nested_zip_count_towards_the_same_limit():
    limits = ArchiveLimits(
        max_members=3, max_member_bytes=10_000, max_total_bytes=100_000, max_ratio=200
    )
    inner = make_zip([(f"f{i}.pdf", b"%PDF-1") for i in range(3)])
    assert _rejected(make_zip([("annexos.zip", inner)]), limits) == "too_many_members"


def test_the_total_uncompressed_size_is_capped():
    limits = ArchiveLimits(
        max_members=20, max_member_bytes=1000, max_total_bytes=1500, max_ratio=200
    )
    data = make_zip(
        [("a.pdf", b"%PDF-" + b"a" * 900), ("b.pdf", b"%PDF-" + b"b" * 900)],
        compression=zipfile.ZIP_STORED,
    )
    assert _rejected(data, limits) == "too_large_uncompressed"
