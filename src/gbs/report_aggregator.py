"""Report Aggregator

Utilities for merging multiple report files into a single self-contained
HTML document with a tabbed interface.

Aggregation modes:
- aggregate_html(): For HTML reports, uses <iframe srcdoc> to isolate styles.
- aggregate_text(): For plain text / mixed content reports using DOM tabs.

Report tab types:
- TextReport: Plain text rendered in <pre>.
- HtmlFragment: Raw HTML rendered directly in a <div>.

Intended for aggregating FPGA tool reports (synthesis, PnR, etc.) into
a single browsable artifact.
"""

from __future__ import annotations
from dataclasses import dataclass
from html import escape
from pathlib import Path


@dataclass
class ReportPage:
    """A single report page to embed in the aggregated document."""
    title: str
    html: str

    @classmethod
    def from_file(cls, path: Path, title: str | None = None) -> ReportPage:
        """Load a report page from an HTML file.

        If title is not provided, attempts to extract it from the HTML
        <title> tag, falling back to the filename.
        """
        html = path.read_text(errors="replace")
        if title is None:
            title = _extract_title(html) or path.stem
        return cls(title=title, html=html)


@dataclass
class TextReport:
    """A plain text report to embed in the aggregated document."""
    title: str
    text: str

    @classmethod
    def from_file(cls, path: Path, title: str | None = None) -> TextReport:
        """Load a text report from a file.

        If title is not provided, falls back to the filename stem.
        """
        text = path.read_text(errors="replace")
        if title is None:
            title = path.stem
        return cls(title=title, text=text)


@dataclass
class HtmlFragment:
    """An HTML fragment to embed directly in a tab pane (no iframe)."""
    title: str
    html: str


def csv_to_html_table(text: str) -> str:
    """Convert a CSV file with comment preamble to HTML.

    Lines starting with '#' are rendered in a <pre> block.
    The first non-comment line is treated as the header row.
    Remaining lines are rendered as table rows.
    Commas are used as delimiters; trailing empty fields are stripped.
    """
    lines = text.splitlines()
    preamble = []
    data_lines = []

    for line in lines:
        if not data_lines and (line.startswith('#') or line.strip() == ''):
            preamble.append(line.lstrip('#'))
        else:
            data_lines.append(line)

    # Separate trailing comment/blank lines from data rows
    footnotes = []
    while data_lines and (data_lines[-1].startswith('#') or data_lines[-1].strip() == ''):
        footnotes.append(data_lines.pop().lstrip('#'))
    footnotes.reverse()

    parts = []

    if preamble:
        parts.append(f'<pre>{escape(chr(10).join(preamble))}</pre>')

    if data_lines:
        def split_row(line):
            fields = line.split(',')
            # Strip trailing empty fields
            while fields and fields[-1].strip() == '':
                fields.pop()
            return fields

        header = split_row(data_lines[0])
        col_count = len(header)
        parts.append('<table>')
        parts.append('<tr>' + ''.join(f'<th>{escape(h.strip())}</th>' for h in header) + '</tr>')
        for row_line in data_lines[1:]:
            fields = split_row(row_line)
            # Pad short rows to match header column count
            fields.extend([''] * (col_count - len(fields)))
            parts.append('<tr>' + ''.join(f'<td>{escape(f.strip())}</td>' for f in fields) + '</tr>')
        parts.append('</table>')

    if footnotes:
        # Strip leading/trailing blank lines from footnotes
        while footnotes and footnotes[0].strip() == '':
            footnotes.pop(0)
        while footnotes and footnotes[-1].strip() == '':
            footnotes.pop()
        if footnotes:
            parts.append(f'<pre>{escape(chr(10).join(footnotes))}</pre>')

    return '\n'.join(parts)


def aggregate_html(pages: list[ReportPage], title: str = "Reports") -> str:
    """Merge multiple HTML pages into a single tabbed HTML document.

    Args:
        pages: List of ReportPage instances to embed.
        title: Title for the aggregated document.

    Returns:
        A self-contained HTML string.
    """
    if not pages:
        return f"<!DOCTYPE html><html><head><title>{escape(title)}</title></head><body><p>No reports.</p></body></html>"

    tab_buttons = []
    tab_frames = []

    for i, page in enumerate(pages):
        active = " active" if i == 0 else ""
        tab_id = f"tab{i}"
        tab_buttons.append(
            f'<button class="tab-btn{active}" onclick="showTab(\'{tab_id}\', this)">'
            f'{escape(page.title)}</button>'
        )
        # Fix anchor navigation for srcdoc iframes: the iframe has no real
        # URL, so href="#id" resolves against the parent and reloads it
        # inside the frame.  A small script intercepts clicks instead.
        html = _fix_srcdoc_anchors(page.html)
        tab_frames.append(
            f'<iframe id="{tab_id}" class="tab-frame{active}" srcdoc="{escape(html)}"></iframe>'
        )

    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; }}
nav {{ display: flex; flex-wrap: wrap; gap: 2px; padding: 4px; background: #f0f0f0; border-bottom: 1px solid #ccc; }}
.tab-btn {{ padding: 6px 14px; border: 1px solid #ccc; border-bottom: none; background: #e8e8e8;
            cursor: pointer; font-size: 13px; border-radius: 4px 4px 0 0; }}
.tab-btn:hover {{ background: #ddd; }}
.tab-btn.active {{ background: #fff; font-weight: bold; border-bottom: 1px solid #fff; margin-bottom: -1px; }}
.tab-frame {{ display: none; flex: 1; width: 100%; border: none; }}
.tab-frame.active {{ display: block; }}
</style>
</head>
<body>
<nav>
{chr(10).join(tab_buttons)}
</nav>
{chr(10).join(tab_frames)}
<script>
function showTab(id, btn) {{
  document.querySelectorAll('.tab-frame').forEach(f => f.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""


def aggregate_text(reports: list[TextReport | HtmlFragment], title: str = "Reports") -> str:
    """Merge multiple reports into a single tabbed HTML document.

    TextReport items are rendered in <pre> blocks.
    HtmlFragment items are rendered directly in <div> blocks.

    Args:
        reports: List of TextReport or HtmlFragment instances to embed.
        title: Title for the aggregated document.

    Returns:
        A self-contained HTML string.
    """
    if not reports:
        return f"<!DOCTYPE html><html><head><title>{escape(title)}</title></head><body><p>No reports.</p></body></html>"

    tab_buttons = []
    tab_panes = []

    for i, report in enumerate(reports):
        active = " active" if i == 0 else ""
        tab_id = f"tab{i}"
        tab_buttons.append(
            f'<button class="tab-btn{active}" onclick="showTab(\'{tab_id}\', this)">'
            f'{escape(report.title)}</button>'
        )
        if isinstance(report, HtmlFragment):
            tab_panes.append(
                f'<div id="{tab_id}" class="tab-pane{active}">{report.html}</div>'
            )
        else:
            tab_panes.append(
                f'<pre id="{tab_id}" class="tab-pane{active}">{escape(report.text)}</pre>'
            )

    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; }}
nav {{ display: flex; flex-wrap: wrap; gap: 2px; padding: 4px; background: #f0f0f0; border-bottom: 1px solid #ccc; }}
.tab-btn {{ padding: 6px 14px; border: 1px solid #ccc; border-bottom: none; background: #e8e8e8;
            cursor: pointer; font-size: 13px; border-radius: 4px 4px 0 0; }}
.tab-btn:hover {{ background: #ddd; }}
.tab-btn.active {{ background: #fff; font-weight: bold; border-bottom: 1px solid #fff; margin-bottom: -1px; }}
.tab-pane {{ display: none; flex: 1; overflow: auto; padding: 12px; font-family: monospace; font-size: 13px; }}
.tab-pane.active {{ display: block; }}
table {{ border-collapse: collapse; margin: 10px 0; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 12px; }}
th {{ background: #e8e8e8; font-weight: bold; }}
tr:nth-child(even) {{ background: #f8f8f8; }}
</style>
</head>
<body>
<nav>
{chr(10).join(tab_buttons)}
</nav>
{chr(10).join(tab_panes)}
<script>
function showTab(id, btn) {{
  document.querySelectorAll('.tab-pane').forEach(f => f.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""


_ANCHOR_FIX_SCRIPT = """\
<script>document.addEventListener("click",function(e){
var a=e.target.closest("a[href^='#']");
if(!a)return;
e.preventDefault();
var id=decodeURIComponent(a.getAttribute("href").slice(1));
var t=document.getElementById(id)||document.getElementsByName(id)[0];
if(t)t.scrollIntoView();
});</script>"""


def _fix_srcdoc_anchors(html: str) -> str:
    """Inject a script that intercepts anchor clicks in srcdoc iframes.

    In a srcdoc iframe there is no real document URL, so the browser
    resolves href="#id" against the parent frame's URL, which reloads
    the parent page inside the iframe.  This script catches those clicks
    and uses scrollIntoView() instead.
    """
    import re
    # Insert before </body>
    result, n = re.subn(
        r"(</body>)",
        _ANCHOR_FIX_SCRIPT + r"\1",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if n:
        return result
    # No </body> — append
    return html + _ANCHOR_FIX_SCRIPT


def _extract_title(html: str) -> str | None:
    """Extract content of <title> tag from HTML string."""
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None
