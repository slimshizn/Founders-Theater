"""A small QR code encoder (ISO/IEC 18004) for the Watch Party lobby's invitation link.

Kodi's Python has no QR or imaging module, so this is one on the standard library:
byte mode with UTF-8 bytes, error correction levels L, M, Q and H, versions 1 to 40
(the smallest that fits), and the standard mask choice by the four penalty rules.
``matrix()`` gives the modules and ``png()`` draws them with a quiet zone, as Android's
TV app shows its invitation (ZXing, level M, a four-module margin).
"""

import itertools
import struct
import zlib

MAX_VERSION = 40
LEVELS = ("L", "M", "Q", "H")
_FORMAT_LEVEL = {"L": 1, "M": 0, "Q": 3, "H": 2}
N1, N2, N3, N4 = 3, 3, 40, 10  # penalty weights

# Error correction codewords per block, and blocks, for versions 1 to 40 (index 0 unused).
_ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
_BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
          8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
          23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
          25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}

_MASKS = (
    lambda row, col: (row + col) % 2 == 0,
    lambda row, col: row % 2 == 0,
    lambda row, col: col % 3 == 0,
    lambda row, col: (row + col) % 3 == 0,
    lambda row, col: (row // 2 + col // 3) % 2 == 0,
    lambda row, col: row * col % 2 + row * col % 3 == 0,
    lambda row, col: (row * col % 2 + row * col % 3) % 2 == 0,
    lambda row, col: ((row + col) % 2 + row * col % 3) % 2 == 0,
)


def _gf_tables():
    """Exponent and logarithm tables of GF(256) with the polynomial 0x11D."""
    exp, log = [0] * 512, [0] * 256
    value = 1
    for power in range(255):
        exp[power] = exp[power + 255] = value
        log[value] = power
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    return exp, log


_EXP, _LOG = _gf_tables()
_GENERATORS = {}


def _multiply(a, b):
    return _EXP[_LOG[a] + _LOG[b]] if a and b else 0


def _generator(degree):
    """The Reed-Solomon generator (x - 1)(x - 2)...(x - 2^(degree-1)), highest power first."""
    if degree not in _GENERATORS:
        poly = [1]
        for power in range(degree):
            root = _EXP[power]
            poly = [high ^ _multiply(low, root) for high, low in zip(poly + [0], [0] + poly)]
        _GENERATORS[degree] = poly
    return _GENERATORS[degree]


def _ecc(block, degree):
    """The ``degree`` error correction codewords for one block of data codewords."""
    generator = _generator(degree)[1:]
    remainder = [0] * degree
    for codeword in block:
        factor = codeword ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            remainder = [value ^ _multiply(term, factor) for value, term in zip(remainder, generator)]
    return remainder


def _size(version):
    return 4 * version + 17


def _alignment_positions(version):
    """Row and column centres of the alignment patterns."""
    if version == 1:
        return []
    count = version // 7 + 2
    step = 26 if version == 32 else (version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
    last = _size(version) - 7
    return [6] + [last - step * i for i in range(count - 2, -1, -1)]


def _raw_codewords(version):
    """Codewords (data and error correction) a symbol holds; the leftover bits are remainder bits."""
    modules = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        modules -= (25 * count - 10) * count - 55
        if version >= 7:
            modules -= 36
    return modules // 8


def _data_codewords(version, level):
    return _raw_codewords(version) - _ECC_PER_BLOCK[level][version] * _BLOCKS[level][version]


def _count_bits(version):
    """Length of byte mode's character count indicator."""
    return 8 if version < 10 else 16


def _fits(length, version, level):
    return 4 + _count_bits(version) + 8 * length <= 8 * _data_codewords(version, level)


def _codewords(data, version, level):
    """Data codewords (mode, count, bytes, terminator, padding), split into blocks and interleaved."""
    capacity = _data_codewords(version, level)
    bits = "0100" + format(len(data), "0{}b".format(_count_bits(version)))
    bits += "".join(format(byte, "08b") for byte in data)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    codewords = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = itertools.cycle((0xEC, 0x11))
    codewords += [next(pad) for _ in range(capacity - len(codewords))]

    blocks, degree = _BLOCKS[level][version], _ECC_PER_BLOCK[level][version]
    raw = _raw_codewords(version)
    short_blocks = blocks - raw % blocks  # the first blocks hold one data codeword fewer
    short_length = raw // blocks - degree
    data_blocks, ecc_blocks, start = [], [], 0
    for index in range(blocks):
        length = short_length + (0 if index < short_blocks else 1)
        block = codewords[start:start + length]
        start += length
        data_blocks.append(block)
        ecc_blocks.append(_ecc(block, degree))
    result = [block[i] for i in range(short_length + 1) for block in data_blocks if i < len(block)]
    result += [block[i] for i in range(degree) for block in ecc_blocks]
    return result


def _format_cells(size):
    """Where format bits 0 to 14 go: the copy around the top-left finder, then the split copy."""
    first = [(row, 8) for row in (0, 1, 2, 3, 4, 5, 7, 8)] + [(8, col) for col in (7, 5, 4, 3, 2, 1, 0)]
    second = [(8, size - 1 - i) for i in range(8)] + [(size - 7 + i, 8) for i in range(7)]
    return first, second


def _format_bits(level, mask):
    """15 format bits: level and mask, BCH (15,5), XOR 0x5412."""
    data = _FORMAT_LEVEL[level] << 3 | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return (data << 10 | remainder) ^ 0x5412


def _version_bits(version):
    """18 version bits: the version and BCH (18,6)."""
    remainder = version
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    return version << 12 | remainder


def _function_patterns(version):
    """(modules, reserved) with the function patterns drawn and the format cells reserved."""
    size = _size(version)
    modules = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def put(row, col, dark):
        modules[row][col] = dark
        reserved[row][col] = True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for centre_row, centre_col in ((3, 3), (3, size - 4), (size - 4, 3)):
        # Finder pattern and its light separator.
        for row in range(centre_row - 4, centre_row + 5):
            for col in range(centre_col - 4, centre_col + 5):
                if 0 <= row < size and 0 <= col < size:
                    put(row, col, max(abs(row - centre_row), abs(col - centre_col)) not in (2, 4))
    positions = _alignment_positions(version)
    corners = {(positions[0], positions[0]), (positions[0], positions[-1]),
               (positions[-1], positions[0])} if positions else set()
    for centre_row in positions:
        for centre_col in positions:
            if (centre_row, centre_col) in corners:
                continue  # these would overlap the finder patterns
            for row in range(centre_row - 2, centre_row + 3):
                for col in range(centre_col - 2, centre_col + 3):
                    put(row, col, max(abs(row - centre_row), abs(col - centre_col)) != 1)
    for cells in _format_cells(size):
        for row, col in cells:
            put(row, col, False)
    put(size - 8, 8, True)  # the dark module
    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            put(i // 3, size - 11 + i % 3, dark)
            put(size - 11 + i % 3, i // 3, dark)
    return modules, reserved


def _place(modules, reserved, codewords):
    """Lay the codeword bits in two-column strips from the bottom right, zigzagging up and down."""
    size = len(modules)
    bits = "".join(format(codeword, "08b") for codeword in codewords)
    index, upward = 0, True
    for right in range(size - 1, 0, -2):
        right = right - 1 if right <= 6 else right  # step over the vertical timing pattern
        for row in (range(size - 1, -1, -1) if upward else range(size)):
            for col in (right, right - 1):
                if not reserved[row][col] and index < len(bits):
                    modules[row][col] = bits[index] == "1"
                    index += 1
        upward = not upward
    # Modules left over are the remainder bits: light before masking.


def _line_penalty(line):
    """Rules 1 and 3 for one row or column; the area outside the symbol counts as light."""
    runs = [0]  # run lengths, alternating light and dark, starting with a (maybe empty) light run
    for dark in line:
        if dark == (len(runs) % 2 == 0):
            runs[-1] += 1
        else:
            runs.append(1)
    score = sum(N1 + length - 5 for length in runs if length >= 5)
    last = len(runs) - 1
    for i in range(3, last - 1, 2):
        # A dark:light:dark:light:dark run of 1:1:3:1:1 with a light run of at least 4 on one side.
        unit = runs[i - 1]
        if runs[i] == 3 * unit and runs[i - 2] == runs[i + 1] == runs[i + 2] == unit:
            before = i == 3 or runs[i - 3] >= 4 * unit
            after = i + 3 >= last or runs[i + 3] >= 4 * unit
            if before or after:
                score += N3
    return score


def _penalty(modules):
    size = len(modules)
    score = sum(_line_penalty(row) for row in modules)
    score += sum(_line_penalty(column) for column in zip(*modules))
    for top, bottom in zip(modules, modules[1:]):
        for a, b, c, d in zip(top, top[1:], bottom, bottom[1:]):
            if a == b == c == d:
                score += N2
    dark = sum(sum(row) for row in modules)
    total = size * size
    # N4 for every full 5% the dark share is away from 50%.
    return score + abs(dark * 2 - total) * 10 // total * N4


def matrix(text, ecc="M"):
    """The QR code for ``text`` as rows of booleans (True is dark), without a quiet zone.

    ``ecc`` is the error correction level, "L", "M", "Q" or "H". Raises ValueError for an
    unknown level, or when the text's UTF-8 bytes do not fit a version 40 symbol.
    """
    if ecc not in LEVELS:
        raise ValueError("Unknown QR error correction level: {!r}".format(ecc))
    data = text.encode("utf-8")
    version = next((v for v in range(1, MAX_VERSION + 1) if _fits(len(data), v, ecc)), None)
    if version is None:
        raise ValueError("Text of {} bytes is too long for a QR code at level {}".format(len(data), ecc))
    base, reserved = _function_patterns(version)
    _place(base, reserved, _codewords(data, version, ecc))
    size = len(base)
    best, best_score = None, None
    for mask, rule in enumerate(_MASKS):
        candidate = [[base[row][col] != (not reserved[row][col] and rule(row, col)) for col in range(size)]
                     for row in range(size)]
        bits = _format_bits(ecc, mask)
        for cells in _format_cells(size):
            for i, (row, col) in enumerate(cells):
                candidate[row][col] = (bits >> i) & 1 == 1
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(matrix, scale=8, border=4):
    """PNG bytes for ``matrix``: black modules on white, ``border`` light modules on each side,
    each module ``scale`` pixels square (1-bit grayscale)."""
    if scale < 1 or border < 0:
        raise ValueError("QR scale must be at least 1 and border at least 0")
    width = len(matrix[0]) + 2 * border
    rows = [[False] * width] * border
    rows += [[False] * border + [bool(dark) for dark in row] + [False] * border for row in matrix]
    rows += [[False] * width] * border
    raw = bytearray()
    for row in rows:
        bits = "".join(("0" if dark else "1") * scale for dark in row)
        bits += "0" * (-len(bits) % 8)  # pad the scanline to whole bytes
        raw += (b"\x00" + int(bits, 2).to_bytes(len(bits) // 8, "big")) * scale
    header = struct.pack(">IIBBBBB", width * scale, len(rows) * scale, 1, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))
