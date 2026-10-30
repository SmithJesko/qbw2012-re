"""Unit tests for the decoding primitives. Synthetic bytes only, no company files.

Run from the repo root: python3 -I -m unittest discover -s tests
"""
import datetime
import decimal
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import fill11  # noqa: E402
import schema11  # noqa: E402

D = decimal.Decimal


class NumericTest(unittest.TestCase):
    """NUMERIC: [k][ctrl][k base-100 digits, least significant first],
    value = coef * 100^((ctrl & 0x7F) - 64), sign in ctrl & 0x80."""

    def read(self, hexbytes):
        b = bytes.fromhex(hexbytes)
        value, end = schema11.read_numeric(b, 0)
        self.assertEqual(end, len(b))
        return value

    def test_positive_integer(self):
        self.assertEqual(self.read("02 40 08 02"), D(208))
        self.assertEqual(self.read("01 41 03"), D(300))

    def test_scale(self):
        self.assertEqual(self.read("02 3f 2f 4b"), D("75.47"))

    def test_negative(self):
        self.assertEqual(self.read("02 bf 2f 4b"), D("-75.47"))

    def test_zero(self):
        self.assertEqual(self.read("00 40"), D(0))
        self.assertEqual(format(self.read("00 40"), "f"), "0")

    def test_out_of_sync_digit(self):
        with self.assertRaises(ValueError):
            schema11.read_numeric(bytes.fromhex("01 40 64"), 0)


class TextTest(unittest.TestCase):
    def test_short_form(self):
        b = b"\x05Hello" + b"rest"
        self.assertEqual(schema11.read_text(b, 0, 30), ("Hello", 6))

    def test_ff_length_form(self):
        body = b"x" * 300
        b = b"\xff" + len(body).to_bytes(2, "big") + body
        self.assertEqual(schema11.read_text(b, 0, 4096), ("x" * 300, 303))

    def test_short_form_on_long_column(self):
        b = b"\x03abc"
        self.assertEqual(schema11.read_text(b, 0, 4096), ("abc", 4))

    def test_cp1252(self):
        b = b"\x07\x93qbw\x94 \x80"
        self.assertEqual(schema11.read_text(b, 0, 30)[0], "\u201cqbw\u201d \u20ac")

    def test_cp1252_undefined_bytes_are_lossless(self):
        raw = bytes(range(256))
        text = schema11.decode_text(raw)
        back = bytes(ord(ch) if ch in "\x81\x8d\x8f\x90\x9d" else ch.encode("cp1252")[0] for ch in text)
        self.assertEqual(back, raw)
        self.assertEqual(len(set(text)), 256)


class DateTest(unittest.TestCase):
    def date(self, minutes):
        col = dict(domain=6, nullable=False, width=4)
        value, end = schema11.read_value(minutes.to_bytes(4, "little"), 0, col)
        self.assertEqual(end, 4)
        return value

    def test_epoch(self):
        self.assertEqual(self.date(0), datetime.datetime(1600, 2, 29))

    def test_unix_epoch(self):
        # 135,081 days between 1600-02-29 and 1970-01-01 (OpenQBW#22)
        self.assertEqual(self.date(135081 * 1440), datetime.datetime(1970, 1, 1))

    def test_timestamp(self):
        col = dict(domain=13, nullable=False, width=8)
        raw = (135081 * 1440 + 90).to_bytes(4, "little") + (30_500_000).to_bytes(4, "little")
        value, _ = schema11.read_value(raw, 0, col)
        self.assertEqual(value, datetime.datetime(1970, 1, 1, 1, 30, 30, 500000))


class FillTest(unittest.TestCase):
    """stored[i] = plain[i] + (K + pn + si) + i * T[(pn + si + 1) // 2]  (mod 256)"""

    PAGES = 40

    def setUp(self):
        rng = random.Random(2012)
        self.k = 45
        slots = (self.PAGES + 8) // 2 + 1
        self.steps = {s: rng.randrange(1, 256, 2) for s in range(slots)}
        plain = bytearray(rng.randbytes(fill11.PAGE))  # page 0 is not filled
        for _ in range(1, self.PAGES):
            page = bytearray(fill11.PAGE)
            for _ in range(40):  # sparse data, mostly zero bytes like a real page
                off = rng.randrange(0, 0xFF0 - 16)
                page[off:off + 8] = rng.randbytes(8)
            page[0xFF0:] = rng.randbytes(16)  # plaintext trailer
            plain += page
        self.plain = bytes(plain)
        self.stored = self.encode(self.plain)

    def encode(self, plain):
        out = bytearray(plain)
        for pn in range(1, self.PAGES):
            for si in range(8):
                off = pn * fill11.PAGE + si * fill11.SECTOR
                n = fill11.SECTOR if si < 7 else 0xFF0 - 7 * fill11.SECTOR
                base = (self.k + pn + si) & 255
                step = self.steps[(pn + si + 1) // 2]
                for i in range(n):
                    out[off + i] = (out[off + i] + base + i * step) & 255
        return bytes(out)

    def test_round_trip(self):
        self.assertEqual(fill11.decode(self.stored, self.k, self.steps), self.plain)

    def test_recovers_key_and_steps(self):
        k = fill11.find_k(self.stored)
        self.assertEqual(k, self.k)
        steps = fill11.find_steps(self.stored, k)
        self.assertEqual(fill11.decode(self.stored, k, steps), self.plain)


if __name__ == "__main__":
    unittest.main()
