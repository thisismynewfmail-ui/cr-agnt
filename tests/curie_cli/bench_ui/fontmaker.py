"""Build small, real TrueType fonts for the lettering tests.

The lettering code is FreeType reading an outline file, so the tests need font
files — and the ones a machine happens to have are the wrong ones to depend
on. A CI image may have none, a laptop may have four hundred, and none of them
is shaped like the fonts that break things: a face whose family name is not
its file name, one with no lowercase, one with no dash, one encoded for the
Windows symbol page or for the classic Mac, one so wide that nothing fits.

So these are made here, from a five-by-seven pixel alphabet drawn as square
TrueType contours. Every file is a complete, valid ``glyf`` font — ``head``,
``hhea``, ``maxp``, ``hmtx``, ``cmap``, ``loca``, ``glyf``, ``name``, ``post``
— that FreeType opens the way it opens any other, and nothing in it belongs to
anyone else, so it can live in a test directory without a licensing question.

Standard library only: ``struct`` and ``zlib``.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: A five-by-seven alphabet, one string per row, ``#`` for ink. Enough of the
#: letters, figures and marks to set every title the console letters.
PIXELS: Dict[str, Tuple[str, ...]] = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "-": (".....", ".....", ".....", ".###.", ".....", ".....", "....."),
    "—": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
}

#: Font units per pixel of the alphabet, and the em they sit in.
UNIT = 100
UPEM = 1000
ASCENT = 800
DESCENT = 200


def _checksum(data: bytes) -> int:
    padded = data + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF


def _glyph(
    rows: Sequence[str], width_scale: int, dome: bool = False
) -> Tuple[bytes, int, int, int, int, int]:
    """One simple glyph: rectangles for the inked pixels, and maybe a dome.

    Returns ``(data, xmin, ymin, xmax, ymax, points)``. Clockwise contours,
    which is the TrueType winding for ink; overlapping rectangles union under
    the non-zero rule, so adjacent pixels read as one stroke.

    ``dome`` caps the glyph with a quadratic arch whose control point sits a
    pixel above where the arch reaches — the shape of every round letter in a
    real font, and the reason a glyph's box (which counts control points)
    stands taller than its ink.
    """
    contours: List[List[Tuple[int, int, bool]]] = []
    height = len(rows)
    px = UNIT * width_scale
    # One rectangle per run of ink, carried down while the run below matches,
    # rather than a square per pixel: fewer abutting edges for the hinter to
    # pull apart into seams.
    open_runs: Dict[Tuple[int, int], int] = {}
    finished: List[Tuple[int, int, int, int]] = []
    for row_index, row in enumerate(list(rows) + [""]):
        runs = set()
        column = 0
        while column < len(row):
            if row[column] == "#":
                start = column
                while column < len(row) and row[column] == "#":
                    column += 1
                runs.add((start, column))
            else:
                column += 1
        for run, first in list(open_runs.items()):
            if run not in runs:
                finished.append((run[0], run[1], first, row_index))
                del open_runs[run]
        for run in runs:
            open_runs.setdefault(run, row_index)
    for start, end, first, last in finished:
        left, right = start * px, end * px
        top = (height - first) * UNIT
        bottom = (height - last) * UNIT
        contours.append(
            [(left, bottom, True), (left, top, True), (right, top, True), (right, bottom, True)]
        )
    if dome and contours:
        left = min(x for contour in contours for x, _y, _on in contour)
        right = max(x for contour in contours for x, _y, _on in contour)
        top = height * UNIT
        # On, off, on over the top, then straight back underneath: the arch
        # rises one pixel, its control point two.
        contours.append(
            [(left, top, True), ((left + right) // 2, top + 2 * UNIT, False), (right, top, True)]
        )
    if not contours:
        return b"", 0, 0, 0, 0, 0
    xs = [x for contour in contours for x, _y, _on in contour]
    ys = [y for contour in contours for _x, y, _on in contour]
    xmin, ymin, xmax, ymax = min(xs), min(ys), max(xs), max(ys)
    out = struct.pack(">hhhhh", len(contours), xmin, ymin, xmax, ymax)
    ends, total = [], 0
    for contour in contours:
        total += len(contour)
        ends.append(total - 1)
    out += struct.pack(f">{len(ends)}H", *ends)
    out += struct.pack(">H", 0)  # no instructions
    # Bit 0 says on the curve; every coordinate is a two-byte delta.
    out += bytes(0x01 if on else 0x00 for contour in contours for _x, _y, on in contour)
    last = 0
    for contour in contours:
        for x, _y, _on in contour:
            out += struct.pack(">h", x - last)
            last = x
    last = 0
    for contour in contours:
        for _x, y, _on in contour:
            out += struct.pack(">h", y - last)
            last = y
    return out, xmin, ymin, xmax, ymax, total


def _cmap_format4(mapping: Mapping[int, int]) -> bytes:
    codes = sorted(code for code in mapping if code < 0xFFFF)
    segments: List[Tuple[int, int, int]] = []
    for code in codes:
        glyph = mapping[code]
        if segments:
            start, end, delta = segments[-1]
            if code == end + 1 and (glyph - code) == delta:
                segments[-1] = (start, code, delta)
                continue
        segments.append((code, code, glyph - code))
    segments.append((0xFFFF, 0xFFFF, 1))
    count = len(segments)
    search = 2 ** (count.bit_length() - 1) * 2
    selector = (search // 2).bit_length() - 1
    shift = count * 2 - search
    body = struct.pack(">HHHH", count * 2, search, selector, shift)
    body += struct.pack(f">{count}H", *(end for _s, end, _d in segments))
    body += struct.pack(">H", 0)
    body += struct.pack(f">{count}H", *(start for start, _e, _d in segments))
    body += struct.pack(f">{count}h", *(((delta + 0x8000) % 0x10000) - 0x8000 for _s, _e, delta in segments))
    body += struct.pack(f">{count}H", *([0] * count))
    return struct.pack(">HHH", 4, 6 + len(body), 0) + body


def _cmap_format0(mapping: Mapping[int, int]) -> bytes:
    table = bytearray(256)
    for code, glyph in mapping.items():
        if 0 <= code < 256:
            table[code] = glyph if glyph < 256 else 0
    return struct.pack(">HHH", 0, 262, 0) + bytes(table)


def _name_table(names: Mapping[int, str]) -> bytes:
    records, strings = [], b""
    for platform, encoding, language, codec in ((1, 0, 0, "mac_roman"), (3, 1, 0x409, "utf-16-be")):
        for name_id in sorted(names):
            raw = names[name_id].encode(codec, "replace")
            records.append((platform, encoding, language, name_id, len(raw), len(strings)))
            strings += raw
    header = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    return header + b"".join(struct.pack(">6H", *record) for record in records) + strings


def build_font(
    path: "str | Path",
    *,
    family: str = "Glyphic Test",
    style: str = "Regular",
    chars: Optional[Iterable[str]] = None,
    lowercase: bool = True,
    encoding: str = "unicode",
    width_scale: int = 1,
    space: bool = True,
    dome: bool = False,
) -> Path:
    """Write a font to ``path`` and return it.

    ``chars`` limits the alphabet (default: all of :data:`PIXELS`).
    ``lowercase`` maps a-z onto copies of the capitals, the way most display
    faces with one case do; off, the face has no lowercase at all.
    ``encoding`` is ``unicode`` (a Windows Unicode cmap), ``symbol`` (the
    Windows symbol page, with every character at U+F0xx and nothing else), or
    ``mac`` (a classic Mac Roman cmap and nothing else).
    ``width_scale`` stretches every pixel sideways, for a face too wide to fit.
    ``space`` gives the face a space glyph; off, it has none.
    ``dome`` arches the top of every glyph with a curve whose control point
    stands above its ink, as a real font's round letters do.
    """
    wanted = [ch for ch in (chars if chars is not None else PIXELS) if ch in PIXELS]
    glyph_rows: List[Tuple[str, ...]] = []
    mapping: Dict[int, int] = {}
    # 0 .notdef: a hollow box, so a missing character draws something visible.
    glyph_rows.append(("#####", "#...#", "#...#", "#...#", "#...#", "#...#", "#####"))
    if space:
        glyph_rows.append(())
        mapping[0x20] = 1
    for ch in wanted:
        glyph_rows.append(PIXELS[ch])
        mapping[ord(ch)] = len(glyph_rows) - 1
        if lowercase and ch.isalpha() and ch.isupper():
            glyph_rows.append(PIXELS[ch])
            mapping[ord(ch.lower())] = len(glyph_rows) - 1

    glyf, loca, metrics = b"", [0], []
    max_points = max_contours = 0
    xmin = ymin = 10 ** 6
    xmax = ymax = -(10 ** 6)
    advance = (5 * width_scale + 1) * UNIT
    for rows in glyph_rows:
        data, gx0, gy0, gx1, gy1, points = _glyph(rows, width_scale, dome)
        data += b"\0" * (-len(data) % 4)
        glyf += data
        loca.append(len(glyf))
        metrics.append((advance, gx0))
        if data:
            xmin, ymin = min(xmin, gx0), min(ymin, gy0)
            xmax, ymax = max(xmax, gx1), max(ymax, gy1)
            max_points = max(max_points, points)
            max_contours = max(max_contours, data and struct.unpack(">h", data[:2])[0] or 0)
    count = len(glyph_rows)

    if encoding == "symbol":
        subtables = [((3, 0), _cmap_format4({0xF000 + code: glyph for code, glyph in mapping.items() if code < 0x100}))]
    elif encoding == "mac":
        roman = {}
        for code, glyph in mapping.items():
            try:
                byte = chr(code).encode("mac_roman")
            except UnicodeEncodeError:
                continue
            roman[byte[0]] = glyph
        subtables = [((1, 0), _cmap_format0(roman))]
    else:
        subtables = [((0, 3), _cmap_format4(mapping)), ((3, 1), _cmap_format4(mapping))]
    cmap = struct.pack(">HH", 0, len(subtables))
    offset = 4 + 8 * len(subtables)
    blobs = b""
    for (platform, enc), blob in subtables:
        cmap += struct.pack(">HHI", platform, enc, offset + len(blobs))
        blobs += blob
    cmap += blobs

    full = family if style.lower() in ("regular", "") else f"{family} {style}"
    tables = {
        b"head": struct.pack(
            ">IIIIHHqqhhhhHHhhh",
            0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0x000B, UPEM, 0, 0,
            xmin, ymin, xmax, ymax, 0, 8, 2, 1, 0,
        ),
        b"hhea": struct.pack(
            ">IhhhHhhhhhhhhhhhH",
            0x00010000, ASCENT, -DESCENT, 0, advance, 0, 0, xmax, 1, 0, 0,
            0, 0, 0, 0, 0, count,
        ),
        b"maxp": struct.pack(
            ">IHHHHHHHHHHHHHH",
            0x00010000, count, max_points, max_contours, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0,
        ),
        b"hmtx": b"".join(struct.pack(">Hh", width, lsb) for width, lsb in metrics),
        b"cmap": cmap,
        b"loca": struct.pack(f">{len(loca)}I", *loca),
        b"glyf": glyf,
        b"name": _name_table({
            1: family,
            2: style,
            4: full,
            6: full.replace(" ", "") or "Glyphic",
        }),
        b"post": struct.pack(">IIhhIIIII", 0x00030000, 0, -100, 50, 0, 0, 0, 0, 0),
    }
    return _write_sfnt(Path(path), tables)


def _write_sfnt(path: Path, tables: Mapping[bytes, bytes]) -> Path:
    tags = sorted(tables)
    count = len(tags)
    search = 2 ** (count.bit_length() - 1) * 16
    selector = (search // 16).bit_length() - 1
    header = struct.pack(">IHHHH", 0x00010000, count, search, selector, count * 16 - search)
    offset = 12 + 16 * count
    directory, body = b"", b""
    for tag in tags:
        data = tables[tag]
        directory += struct.pack(">4sIII", tag, _checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    font = bytearray(header + directory + body)
    # checkSumAdjustment, at byte 8 of head, makes the whole file sum to the magic.
    head_offset = struct.unpack(">I", font[12 + 16 * tags.index(b"head") + 8: 12 + 16 * tags.index(b"head") + 12])[0]
    adjustment = (0xB1B0AFBA - _checksum(bytes(font))) & 0xFFFFFFFF
    font[head_offset + 8: head_offset + 12] = struct.pack(">I", adjustment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(font))
    return path


def build_woff(sfnt_path: "str | Path", woff_path: "str | Path") -> Path:
    """Wrap a TrueType file as WOFF 1.0 — compressed tables, same font."""
    data = Path(sfnt_path).read_bytes()
    flavor, count = struct.unpack(">IH", data[:6])
    entries = []
    for index in range(count):
        tag, checksum, offset, length = struct.unpack(">4sIII", data[12 + 16 * index: 28 + 16 * index])
        raw = data[offset: offset + length]
        packed = zlib.compress(raw)
        if len(packed) >= len(raw):
            packed = raw
        entries.append((tag, checksum, raw, packed))
    offset = 44 + 20 * count
    directory, body = b"", b""
    for tag, checksum, raw, packed in entries:
        directory += struct.pack(">4sIIII", tag, offset + len(body), len(packed), len(raw), checksum)
        body += packed + b"\0" * (-len(packed) % 4)
    total_sfnt = 12 + 16 * count + sum(len(raw) + (-len(raw) % 4) for _t, _c, raw, _p in entries)
    header = struct.pack(
        ">4sIIHHIHHIIIII",
        b"wOFF", flavor, 44 + len(directory) + len(body), count, 0, total_sfnt,
        1, 0, 0, 0, 0, 0, 0,
    )
    out = Path(woff_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + directory + body)
    return out


__all__ = ["PIXELS", "build_font", "build_woff"]
