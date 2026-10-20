"""Stream the Iowa Liquor Sales yearly files and keep only a small weekly-per-store summary.

Why: the Iowa Data Hub has no server-side filter and each yearly file is several hundred MB.
This script never stores the full file. It streams the zipped CSV over HTTPS, decompresses on
the fly, aggregates per (store, ISO week) and writes one small CSV.

Data: Iowa Liquor Sales, State of Iowa Department of Revenue, licence CC BY 4.0.
Each row is a PURCHASE by a licensed retailer (store) from the state wholesaler, not a retail
sale to a consumer. Treat it as the store's purchasing (cost of goods) history.

Run (PowerShell or Git Bash, from the repo root; needs only the standard library):
    python -I docs/research/scripts/iowa_store_weekly.py data/raw/iowa_liquor/iowa_store_weekly_2020_2024.csv

Network use: about 5 files of several hundred MB each. Run it once, on a good connection.
Status: logic self-tested on a tiny local zip (see --selftest); NOT yet run against the live
files, and the live column names are unconfirmed (the script matches them case-insensitively).
"""
import codecs
import csv
import io
import struct
import sys
import urllib.request
import zlib
from collections import defaultdict
from datetime import datetime

DATASETS = {2020: 1257, 2021: 1258, 2022: 1259, 2023: 1260, 2024: 1261}
URL = "https://idh-be.iowa.gov/api/v1/datasets/{id}/rows.csv"


class _Inflate(io.RawIOBase):
    """Read the first member of a ZIP archive from a byte stream, decompressing on the fly."""

    def __init__(self, stream):
        self.s = stream
        head = self._exact(30)
        sig, _v, _flags, method, _t, _d, _crc, _cs, _us, nlen, elen = struct.unpack("<IHHHHHIIIHH", head)
        if sig != 0x04034B50:
            raise ValueError("not a zip stream")
        self._exact(nlen + elen)
        self.method = method
        self.dec = zlib.decompressobj(-15) if method == 8 else None
        self.buf = b""
        self.done = False

    def _exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.s.read(n - len(data))
            if not chunk:
                raise EOFError("short read")
            data += chunk
        return data

    def readable(self):
        return True

    def readinto(self, b):
        while not self.buf and not self.done:
            raw = self.s.read(1 << 20)
            if not raw:
                self.done = True
                break
            out = self.dec.decompress(raw) if self.dec else raw
            self.buf += out
            if self.dec is not None and self.dec.eof:
                self.done = True
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        return n


def _find(header, *names):
    low = {h.strip().lower(): i for i, h in enumerate(header)}
    for n in names:
        if n in low:
            return low[n]
    raise KeyError(f"none of {names} in header {header[:12]}")


def aggregate(text_stream, agg):
    r = csv.reader(text_stream)
    header = next(r)
    i_date = _find(header, "date")
    i_store = _find(header, "store number", "store_number", "store")
    i_name = _find(header, "store name", "store_name")
    i_city = _find(header, "city")
    i_sale = _find(header, "sale (dollars)", "sale_dollars", "sale")
    i_btl = _find(header, "bottles sold", "bottles_sold")
    for row in r:
        try:
            d = row[i_date][:10]
            dt = datetime.strptime(d, "%m/%d/%Y") if "/" in d else datetime.strptime(d, "%Y-%m-%d")
            iso = dt.isocalendar()
            key = (row[i_store], f"{iso[0]}-W{iso[1]:02d}", dt.strftime("%Y-%m-%d"))
            a = agg[key]
            a[0] += float(row[i_sale] or 0)
            a[1] += int(float(row[i_btl] or 0))
            a[2] += 1
            agg.setdefault(("name", row[i_store]), [row[i_name], row[i_city]])
        except (ValueError, IndexError):
            continue


def run(out_path, open_stream, datasets):
    agg = defaultdict(lambda: [0.0, 0, 0])
    for year, ds in datasets.items():
        with open_stream(ds) as resp:
            raw = io.BufferedReader(_Inflate(resp), 1 << 20)
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            aggregate(text, agg)
        print(year, "done", len(agg), "keys", file=sys.stderr)
    names = {k[1]: v for k, v in agg.items() if k[0] == "name"}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["store_number", "store_name", "city", "iso_week", "purchase_date", "sale_dollars", "bottles", "lines"])
        for k in sorted(k for k in agg if k[0] != "name"):
            v = agg[k]
            n = names.get(k[0], ["", ""])
            w.writerow([k[0], n[0], n[1], k[1], k[2], round(v[0], 2), v[1], v[2]])


def _selftest():
    import tempfile
    import zipfile
    d = tempfile.mkdtemp()
    csvtxt = ("Invoice/Item Number,Date,Store Number,Store Name,City,Bottles Sold,Sale (Dollars)\n"
              "INV-1,01/05/2020,1001,Corner Shop,Ames,6,60.50\n"
              "INV-2,01/05/2020,1001,Corner Shop,Ames,2,20.00\n"
              "INV-3,01/12/2020,1002,Other Shop,Des Moines,1,9.99\n")
    zp = d + "/t.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rows.csv", csvtxt)
    out = d + "/o.csv"
    run(out, lambda ds: open(zp, "rb"), {2020: 1})
    rows = list(csv.reader(open(out, encoding="utf-8")))
    assert rows[1][0] == "1001" and rows[1][5] == "80.5" and rows[1][7] == "2" and len(rows[1]) == 8, rows
    print("selftest ok", rows[1:])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        _selftest()
    else:
        out = sys.argv[1]
        run(out, lambda ds: urllib.request.urlopen(URL.format(id=ds), timeout=300), DATASETS)

