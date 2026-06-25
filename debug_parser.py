"""Debug script for bookmark parser"""
from html.parser import HTMLParser

HTML_CONTENT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000">视频网站</H3>
    <DL><p>
        <DT><A HREF="https://www.youtube.com/" ADD_DATE="1700000001" ICON="">YouTube</A>
        <DT><A HREF="https://www.bilibili.com/" ADD_DATE="1700000002" ICON="">Bilibili</A>
    </DL><p>
    <DT><A HREF="https://github.com/" ADD_DATE="1700000020" ICON="">GitHub</A>
</DL><p>"""


class DebugParser(HTMLParser):
    FOLDER_TAGS = {"h2", "h3", "h4", "h5", "h6", "dt"}

    def __init__(self):
        super().__init__()
        self._results = []
        self._current_text = ""
        self._current_tag = ""
        self._current_attrs = {}
        self._text_from_heading = False
        self._dl_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        self._current_tag = tag
        self._results.append(f"START: <{tag}> attrs={attrs_dict}")

        if tag == "dl":
            self._dl_depth += 1
            self._results.append(f"  -> DL depth now: {self._dl_depth}")
            self._results.append(f"  -> _current_text='{self._current_text}', text_from_heading={self._text_from_heading}, attrs={self._current_attrs}")
        elif tag in self.FOLDER_TAGS:
            self._text_from_heading = True
            self._current_text = ""
            self._current_attrs = {"add_date": attrs_dict.get("add_date", "0")}
            self._results.append(f"  -> FOLDER HEADING: text cleared, attrs={self._current_attrs}")
        elif tag == "a":
            self._current_attrs = {"href": attrs_dict.get("href", ""), "add_date": attrs_dict.get("add_date", "0")}
            self._current_text = ""
            self._text_from_heading = False
            self._results.append(f"  -> A TAG: href={attrs_dict.get('href', '')}")

    def handle_endtag(self, tag):
        tag = tag.lower()
        self._results.append(f"END: </{tag}>")
        if tag == "dl":
            self._dl_depth -= 1
            self._results.append(f"  -> DL depth now: {self._dl_depth}")
        elif tag == "a":
            title = self._current_text.strip()
            href = self._current_attrs.get("href", "")
            self._results.append(f"  -> A END: title='{title}', href='{href}'")
            self._current_text = ""
            self._current_attrs = {}
        elif tag in self.FOLDER_TAGS:
            self._results.append(f"  -> HEADING END: text='{self._current_text.strip()}'")

    def handle_data(self, data):
        self._current_text += data
        if data.strip():
            self._results.append(f"DATA: '{data.strip()}' (raw: {repr(data)})")
            self._results.append(f"  -> _current_text now: '{self._current_text}'")

    def handle_entityref(self, name):
        self._current_text += f"&{name};"

    def handle_charref(self, name):
        self._current_text += f"&#{name};"


parser = DebugParser()
parser.feed(HTML_CONTENT)
print("=== Parser Event Log ===")
for r in parser._results:
    print(r)
