"""The QR code encoder for the Watch Party invitation: matrices against qrencode, and the PNG."""

import hashlib
import struct
import unittest
import zlib

from resources.lib.silo import qrcode

URL = "https://silo.example/rooms/join?token=ABCDEFGHJKLMNPQRSTUVWXYZ"
LONG_URL = "https://silo.example/rooms/join?token=" + "ABCDEFGHJKLMNPQRSTUVWXYZ" * 4

# Reference matrices from libqrencode 4.1.1 (qrencode -t ASCII -l <level> -m 0 -8 '<text>'),
# one string per row, "#" dark and "." light.

# "HELLO WORLD" at L: version 1.
HELLO_L = (
    "#######.#####.#######",
    "#.....#.#.#.#.#.....#",
    "#.###.#.#.....#.###.#",
    "#.###.#.####..#.###.#",
    "#.###.#.....#.#.###.#",
    "#.....#.##.#..#.....#",
    "#######.#.#.#.#######",
    ".........####........",
    "##..###..#.#...#.####",
    "..#.##..#####....####",
    "##..#.#..###.##.#..#.",
    "#.#.#....#...#.......",
    "#..#..#.#...#.##..##.",
    "........##..####.#.##",
    "#######..##.#.#.##.#.",
    "#.....#.#.####.##..##",
    "#.###.#.#.##.##...##.",
    "#.###.#.....#...##.##",
    "#.###.#..#.#...###...",
    "#.....#.#.##.#.......",
    "#######.#.#######.#.#",
)

# "HELLO WORLD" at Q: version 1, which it fills exactly.
HELLO_Q = (
    "#######.#..#..#######",
    "#.....#.###...#.....#",
    "#.###.#.#.....#.###.#",
    "#.###.#.#.##..#.###.#",
    "#.###.#.##..#.#.###.#",
    "#.....#..#....#.....#",
    "#######.#.#.#.#######",
    "........#..##........",
    ".##.#.##....#.#.#####",
    "..####...#....##...#.",
    "......#..#####.######",
    "#..###.####.#...#..#.",
    ".#.##.#.#.##.####.#..",
    "........###.##....##.",
    "#######.##.....##.###",
    "#.....#..####..#....#",
    "#.###.#.###.#.#.#.#..",
    "#.###.#...##..###.##.",
    "#.###.#.#.#.#.#.#.#.#",
    "#.....#.#..#....#..#.",
    "#######...###.##..###",
)

# URL at M: version 4, which it fills exactly.
URL_M = (
    "#######.##....#..#...##...#######",
    "#.....#.##...##.#.##.##.#.#.....#",
    "#.###.#.#.#.###.....##.#..#.###.#",
    "#.###.#..#..#..#...#...##.#.###.#",
    "#.###.#.#.####.###...#.#..#.###.#",
    "#.....#...#.....#.#....##.#.....#",
    "#######.#.#.#.#.#.#.#.#.#.#######",
    ".........#.#.#....###.##.........",
    "#..######.....#.##.#....##..#.###",
    "####.....###.###..####.##...###..",
    "..##..#....#####..#..#.#..#.#####",
    ".##.#..#..#.#..####.#.#..#.#..#..",
    "#.#.#.###.#.###..#.#.#..#.###...#",
    "#...##.##..#.##.##.#.#...###.#...",
    "##.#..#.##.##.##.#.#.##...#.##...",
    "###.##..####.#.#...#..#..#.#.##.#",
    "..#...#....#.###...##.########...",
    "#.####.##.####.#.######..##.#.###",
    ".##.#######.#.##..###.######.##.#",
    ".#..##..#...##.##..#.....#..###..",
    "#.##..###..###.......#.##....#..#",
    "#.#.#..######....#....#...#.#....",
    "#.#...#####.#.##.....#.##.###..##",
    "#.#.##.....#...#.#..#..#.###..#.#",
    "####..#..#.....##...#..#######...",
    "........##.##.##..#..#..#...#.#..",
    "#######.#####..#...#..###.#.#.##.",
    "#.....#.#..#..###.#.#..##...###..",
    "#.###.#.#..###.###..#########..#.",
    "#.###.#.#...#.#.....###.##.#..###",
    "#.###.#..#..##.#.#.###.#.#.##.###",
    "#.....#...#.#.#.#..#..#.#########",
    "#######.###....#.#..#..##.###....",
)

# URL at H: version 7, the first with version information.
URL_H = (
    "#######...#.....###.......##.#####..#.#######",
    "#.....#.##...#....#..#..#.#.#......#..#.....#",
    "#.###.#...##.....###..##..###.#..#.#..#.###.#",
    "#.###.#..#.##..#....#.###.###.#....##.#.###.#",
    "#.###.#...#.#.#####.######.#.###..###.#.###.#",
    "#.....#.########..###...#.##...#.#....#.....#",
    "#######.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#######",
    "........###...##.####...#.#...#..............",
    "....####.##..#.##.########..#...###.#.##...#.",
    ".#..##....###.#.#.######.#..#.###..##.###.###",
    "#.#.#.#.##.###.....#.#.#.##...####..##.###.#.",
    "#####......####..#.##..#.####.#.#.#....##..#.",
    "####..#...#.##...##.#.#..##.###.##.#.###.####",
    "...#.....##..##...#..#.##...###.#..##.#....#.",
    "##...##.##....###.##.#..#..###.#.#..###.##...",
    ".#.###...#.#.#.#.#.#..#...#.#.#.#.#.####...##",
    "#.##.##.#.#....#.####.......#...#.....##.#.##",
    "..#.##..#.#.#..#.#.#####..####...#.##.######.",
    "#..#.##.#.##..##....##..#.##.#.###.#.##....#.",
    "###.......#..#.##.##....###..#..##..#.##....#",
    "...######...##..#..#########.##.#...######...",
    "#...#...###.#######.#...#..##.#.##.##...##.#.",
    "...##.#.#####..#.#..#.#.#####...#####.#.#.##.",
    "###.#...#.#.###..####...########..###...#...#",
    "##########.##..#..########..#.#.###.#####....",
    "#.#......########..#.#...######.##.##.##..#.#",
    "#.#.###.##..#..##.....#.#.#.#...#.#.##..####.",
    "..#.#..#.........#.####.###..##....##.###...#",
    "#.##..#.#..##.#......#.#.#.#..#.###...###.#..",
    "#...#..#.###...#..#.##...##.##...#....##..###",
    "...##.###.#.#.#...##.##.#######.#..#.#.##..#.",
    "#..###.##.#.####.##..#.#........#..##...#..#.",
    "#...###..###....##...#......##..###.#.##.##..",
    ".#####.#.###.####...##..#...###..#.#.###.##..",
    "....#.####...##..###.#...###....#..###.#.#.#.",
    ".####...##.##..###...#.#.#.#.##.#....#.##..#.",
    "#..##.#.#..#.##..#.#######.##.#.###.######...",
    "........###.##.###.##...#..#####.#..#...#..#.",
    "#######.##...#.#...##.#.#####....#..#.#.##...",
    "#.....#.##.#....##..#...###.##..##..#...#..##",
    "#.###.#.##.##..#.#.#######..#.#.#.#######....",
    "#.###.#..##..###.#..#....####....#...##......",
    "#.###.#..#..#..#..###...#.###....##..##.###..",
    "#.....#..#.#...##..#.#...#####.#.#.#.........",
    "#######..####.####..#..#..##.##.#...#.......#",
)

# LONG_URL at M: version 8, with blocks of two lengths.
LONG_URL_M = (
    "#######.#.#.##...####.#..##.###.#.##.#..#.#######",
    "#.....#....#..#....#.#####...#...#.#.####.#.....#",
    "#.###.#.#..##..#.#.#.#..##..##########.##.#.###.#",
    "#.###.#..###..#.####......##..##.#.#.#.#..#.###.#",
    "#.###.#....#.###.#.########...###.#.#.....#.###.#",
    "#.....#.#.####...#.#.##...#...###.##..#...#.....#",
    "#######.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#######",
    ".........#.##..##.....#...#...##...#..#.#........",
    "#.#...##.##.##.#...########.###.##..#...#..#..#.#",
    "..##...##.####..##.#..##.#.###.###.#.#.#.#.##....",
    "#..######..########..#####.##.#.#.##########..#.#",
    ".###....#..#....###..#.#.#..######..#.#...##.#.#.",
    "#.###.##.#.####.#..##.#.###.#.#.##..##..##..#.###",
    "##.##..##.#.#..##.########.###.###...#.#...#.#.##",
    ".####.##......##...#.#..###..##..###.##.##..##..#",
    "........#...##...######..#...##..#.#....##...#...",
    "......##.###.#...#.####.###..#......#.####..####.",
    ".####..##..#...#####.#.###..###.##.#.#.#.#.#.#...",
    ".#....####...#...###.##.#.#..##...#.#.##.##.###.#",
    ".##..#.#....##.##.#..#.#####.##..##..##.##.###.#.",
    "....#.#..######..#.###.###..#...#.#.####.#.##...#",
    "#.####.####.#.#..#.#..#.###.##.###...#.#.#.##..#.",
    "#...########.#....#.#.########.#....##..######..#",
    "#####...###.#..#.####.#...#...##...#.####...##...",
    "..#.#.#.#.......#..#.##.#.#.#.#.#...#####.#.#####",
    "...##...#.###.#.##.#.##...#.##..##...#..#...#....",
    ".##.#####..##.##....#########.#.#.#####.######..#",
    "##.#...#..##.##.#.##.#.##...###.##.##...##...#.#.",
    "..#..##.#.#.##..###.#####..###.##...#.###..##.#..",
    "..###....#.##.#..#....########.#.#.#.#.##..#.#..#",
    "#..##.#..######..##.##.#..###.#.###.###...##....#",
    ".#.###.##.###.#.###...###.##......##....#...##..#",
    "#..##.####.##..#.#.####.#.###.#####.##.##...###.#",
    ".##..#.####.#.#.#..#..##.#.#....##...#...###.#...",
    "..###.#.#.....####..####..###.##..#.#.#.#######.#",
    "###.##.##....#.##.#..###...#.##......##.#.##.#...",
    ".##.#.##.####..######..##.#.#...###.#..##.###...#",
    "#.#....#..#.#.##...#..##.#.#.#..##.##....###...#.",
    ".#...##...##..#..####..#..####.#.#.###....###...#",
    ".###...#######.###.###.#.#....##.#.#.####..#.#..#",
    "###...######..######.######.##..###.#.#########..",
    "........###.#..#.###.##...##.#.#.#..##.##...#..##",
    "#######.#...#######...#.#.#####.#.#..##.#.#.#..##",
    "#.....#....#..#.#.#.#.#...###...#.#.#..##...##..#",
    "#.###.#..#...#.##.###.#####.###.###.##..#####.#..",
    "#.###.#..#####..#..##...#..#.#...#..##...#.##.#..",
    "#.###.#.#.#....##..#.#..#.#...#..##..##.#...####.",
    "#.....#..###.#...####.##.....###..##...#.#...#...",
    "#######.#...#.#...###.##..#..#..#.#.#.....#####.#",
)


def rows(grid):
    return tuple("".join("#" if dark else "." for dark in row) for row in grid)


def mask_of(grid):
    """The mask number in a matrix's format information (the copy around the top-left finder)."""
    cells = [(row, 8) for row in (0, 1, 2, 3, 4, 5, 7, 8)] + [(8, col) for col in (7, 5, 4, 3, 2, 1, 0)]
    bits = sum(int(grid[row][col]) << i for i, (row, col) in enumerate(cells)) ^ 0x5412
    return (bits >> 10) & 0b111


class MatrixTests(unittest.TestCase):
    def test_matrices_match_qrencode(self):
        for text, level, expected in (("HELLO WORLD", "L", HELLO_L), ("HELLO WORLD", "Q", HELLO_Q),
                                      (URL, "M", URL_M), (URL, "H", URL_H), (LONG_URL, "M", LONG_URL_M)):
            with self.subTest(text=text, level=level):
                self.assertEqual(rows(qrcode.matrix(text, level)), expected)

    def test_a_version_10_matrix_matches_qrencode(self):
        # Version 10 is the first with a 16-bit character count; SHA-256 of qrencode's rows.
        grid = qrcode.matrix("https://silo.example/rooms/join?token=" + "ABCDEFGHJKLMNPQRSTUVWXYZ" * 7, "M")
        self.assertEqual(len(grid), 57)
        self.assertEqual(hashlib.sha256("\n".join(rows(grid)).encode("ascii")).hexdigest(),
                         "7dd2ee525a51c6800cf0395cd94a127ef0f9ceff569f85c29de2124cef5cc1d4")

    def test_the_default_level_is_m(self):
        self.assertEqual(qrcode.matrix(URL), qrcode.matrix(URL, "M"))

    def test_the_smallest_version_that_fits_is_chosen(self):
        self.assertEqual(len(qrcode.matrix("A" * 14)), 21)  # version 1 at M holds 14 bytes
        self.assertEqual(len(qrcode.matrix("A" * 15)), 25)
        self.assertEqual(len(qrcode.matrix("A" * 7, "H")), 21)
        self.assertEqual(len(qrcode.matrix("A" * 8, "H")), 25)
        # Byte mode counts UTF-8 bytes: "é" is two.
        self.assertEqual(len(qrcode.matrix("é" * 7)), 21)
        self.assertEqual(len(qrcode.matrix("é" * 8)), 25)
        self.assertEqual(len(qrcode.matrix("a" * 2331)), 177)  # version 40 at M holds 2331 bytes

    def test_text_that_does_not_fit_raises(self):
        for text, level in (("a" * 2332, "M"), ("a" * 2954, "L"), ("a" * 1274, "H"), ("é" * 1166, "M")):
            with self.subTest(length=len(text), level=level):
                with self.assertRaises(ValueError):
                    qrcode.matrix(text, level)

    def test_an_unknown_level_raises(self):
        for level in ("X", "", None):
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    qrcode.matrix(URL, level)

    def test_the_dark_share_scores_whole_five_percent_steps(self):
        # Mask 6 leaves 242 of 441 modules dark (54.9%), under 5% from half, so it gets no
        # rule 4 penalty and scores lowest. qrencode 4.1.1 rounds the share to 55% first,
        # scores it 10, and picks mask 1 instead.
        self.assertEqual(mask_of(qrcode.matrix("mK'Y[:bEDu[2(B", "M")), 6)

    def test_format_information_names_the_level(self):
        cells = [(row, 8) for row in (0, 1, 2, 3, 4, 5, 7, 8)] + [(8, col) for col in (7, 5, 4, 3, 2, 1, 0)]
        for level, bits in (("L", 0b01), ("M", 0b00), ("Q", 0b11), ("H", 0b10)):
            with self.subTest(level=level):
                grid = qrcode.matrix(URL, level)
                value = sum(int(grid[row][col]) << i for i, (row, col) in enumerate(cells)) ^ 0x5412
                self.assertEqual(value >> 13, bits)


def chunks(data):
    """(type, body, crc) for each chunk after the PNG signature."""
    offset, result = 8, []
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])
        result.append((kind, body, crc))
        offset += 12 + length
    return result


class PngTests(unittest.TestCase):
    def test_signature_chunks_and_crcs(self):
        data = qrcode.png(qrcode.matrix("HELLO WORLD"))
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        parts = chunks(data)
        self.assertEqual([kind for kind, _, _ in parts], [b"IHDR", b"IDAT", b"IEND"])
        for kind, body, crc in parts:
            self.assertEqual(crc, zlib.crc32(kind + body) & 0xFFFFFFFF, kind)
        self.assertEqual(parts[-1][1], b"")

    def test_the_image_is_the_matrix_plus_the_quiet_zone_scaled(self):
        for scale, border in ((8, 4), (3, 2), (1, 0)):
            with self.subTest(scale=scale, border=border):
                grid = qrcode.matrix(URL)
                kind, header, _ = chunks(qrcode.png(grid, scale=scale, border=border))[0]
                side = (len(grid) + 2 * border) * scale
                self.assertEqual(struct.unpack(">IIBBBBB", header), (side, side, 1, 0, 0, 0, 0))

    def test_the_defaults_are_scale_8_and_border_4(self):
        grid = qrcode.matrix(URL)
        self.assertEqual(qrcode.png(grid), qrcode.png(grid, scale=8, border=4))
        width, height = struct.unpack(">II", chunks(qrcode.png(grid))[0][1][:8])
        self.assertEqual((width, height), ((33 + 8) * 8, (33 + 8) * 8))

    def test_pixels_are_black_for_dark_modules_and_white_elsewhere(self):
        grid, scale, border = qrcode.matrix(URL), 3, 2
        parts = chunks(qrcode.png(grid, scale=scale, border=border))
        width, height = struct.unpack(">II", parts[0][1][:8])
        raw = zlib.decompress(b"".join(body for kind, body, _ in parts if kind == b"IDAT"))
        stride = 1 + (width + 7) // 8
        self.assertEqual(len(raw), stride * height)
        size = len(grid)
        for y in range(height):
            line = raw[y * stride:(y + 1) * stride]
            self.assertEqual(line[0], 0)  # filter type None
            pixels = [(line[1 + x // 8] >> (7 - x % 8)) & 1 for x in range(width)]
            row = y // scale - border
            expected = [0 if 0 <= row < size and 0 <= x // scale - border < size
                        and grid[row][x // scale - border] else 1 for x in range(width)]
            self.assertEqual(pixels, expected, "pixel row {}".format(y))

    def test_a_bad_scale_or_border_raises(self):
        grid = qrcode.matrix("HELLO WORLD")
        for scale, border in ((0, 4), (8, -1)):
            with self.subTest(scale=scale, border=border):
                with self.assertRaises(ValueError):
                    qrcode.png(grid, scale=scale, border=border)


if __name__ == "__main__":
    unittest.main()
