"""Small HTML helpers for the design tests (standard library only)."""

import re
from html.parser import HTMLParser


class Page(HTMLParser):
    """Collects every element's tag and attributes, plus the page text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text_parts = []
        self._skip = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._skip += 1

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

    @property
    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()

    @property
    def ids(self):
        return {attrs["id"] for _, attrs in self.elements if attrs.get("id")}

    def find_all(self, tag=None, **attrs):
        found = []
        for name, element_attrs in self.elements:
            if tag and name != tag:
                continue
            if all(element_attrs.get(key.rstrip("_")) == value for key, value in attrs.items()):
                found.append(element_attrs)
        return found

    def with_class(self, class_name):
        return [attrs for _, attrs in self.elements if class_name in (attrs.get("class") or "").split()]

    def hrefs(self):
        return [attrs["href"] for tag, attrs in self.elements if tag == "a" and attrs.get("href")]


def page(response):
    return Page(response.get_data(as_text=True))
