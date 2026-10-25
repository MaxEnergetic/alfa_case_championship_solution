"""Small, non-executing HTML reader for public registry search tables."""

from html.parser import HTMLParser
import re


class Node:
    def __init__(self, tag="root", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def find(self, tag=None, *, cls=None, id=None):
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if ((tag is None or child.tag == tag)
                    and (cls is None or cls in (child.attrs.get("class") or "").split())
                    and (id is None or child.attrs.get("id") == id)):
                yield child
            yield from child.find(tag, cls=cls, id=id)

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return re.sub(r"\s+", " ", " ".join(
            c.text() if isinstance(c, Node) else c for c in self.children)).strip()


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            if len(self.stack) > 200:
                raise ValueError("HTML nesting exceeds limit")
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def html_text(html):
    return Tree(html).root.text()
