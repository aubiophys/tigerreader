"""Run report: the run header and every plate read as tables, saved as PDF or printed.

Raw values as read, no data reduction. The report is built
from a saved run folder, so it always shows exactly what is in the ASCII files.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import __version__
from .rundata import RunData, fmt_value

HEADER_KEYS = ["ResultsID", "Description", "PlateID", "Operator", "Instrument", "Started", "Finished", "Status",
               "ReaderSerial", "ReaderID", "Basecode", "ReaderConfig", "ReadMode", "AssayType", "Geometry", "Wells",
               "ReadArea", "Speed", "Mixing", "KineticReads", "KineticInterval_s", "KineticTiming",
               "ChecksumWarning"]


def report_html(rd: RunData) -> str:
    """HTML (Qt rich-text subset) for one run."""
    e = html.escape
    h = rd.header
    small = rd.cols > 12
    title = e(h.get("ResultsID", rd.title))
    out = [f"<h2>{title}</h2>",
           f"<p style='font-size:8pt'>{e(str(rd.folder or ''))}</p>",
           "<table cellspacing='0' cellpadding='2' style='font-size:9pt'>"]
    keys = HEADER_KEYS + sorted((k for k in h if k.startswith("FilterSet")), key=lambda k: int(k[9:] or 0))
    for k in keys:
        if h.get(k):
            out.append(f"<tr><td><b>{e(k)}</b></td><td>{e(h[k])}</td></tr>")
    out.append("</table>")
    cell = "font-size:7pt" if small else "font-size:9pt"
    for fs in range(rd.n_filter_sets):
        nk = rd.n_kinetic(fs)
        for k in range(nk):
            pr = rd.read(fs, k)
            if pr is None:
                continue
            kin = f" · read {k + 1} of {nk}" if nk > 1 else ""
            t = f" · t = {pr.t_s:.1f} s" if nk > 1 else ""
            out.append(f"<h4>Filter set {fs + 1}: {e(pr.label)}{kin}{t}</h4>")
            out.append(f"<table border='1' cellspacing='0' cellpadding='2' style='{cell}'><tr><th></th>"
                       + "".join(f"<th>{c}</th>" for c in range(1, rd.cols + 1)) + "</tr>")
            for r in range(1, rd.rows + 1):
                vals = []
                for c in range(1, rd.cols + 1):
                    if (r, c) not in pr.values:
                        vals.append("<td></td>")
                    else:
                        v = pr.values[(r, c)]
                        vals.append(f"<td align='right'>{fmt_value(v)}</td>")
                out.append(f"<tr><th>{chr(64 + r)}</th>" + "".join(vals) + "</tr>")
            out.append("</table>")
    out.append(f"<p style='font-size:8pt'>TigerReader {e(__version__)} · raw values as read (no data reduction); "
               "***** = overrange; empty cell = not read or not kept. All values are also in raw_long.csv.</p>")
    return "\n".join(out)


def _document(rd: RunData):
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(report_html(rd))
    return doc


def _page_layout(rd: RunData):
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize
    orient = QPageLayout.Landscape if rd.cols > 12 else QPageLayout.Portrait
    return QPageLayout(QPageSize(QPageSize.Letter), orient, QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter)


def write_pdf(rd: RunData, path: str | Path) -> Path:
    """Save the report as PDF (needs a running Qt application)."""
    from PySide6.QtGui import QPdfWriter
    path = Path(path)
    writer = QPdfWriter(str(path))
    writer.setPageLayout(_page_layout(rd))
    writer.setTitle(f"{rd.header.get('Instrument', 'FLx800')} run {rd.header.get('ResultsID', '')}")
    writer.setCreator(f"TigerReader {__version__}")
    _document(rd).print_(writer)
    return path


def print_report(rd: RunData, printer) -> None:
    """Print to a QPrinter chosen by the user."""
    printer.setPageLayout(_page_layout(rd))
    _document(rd).print_(printer)
