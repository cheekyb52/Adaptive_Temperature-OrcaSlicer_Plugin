# SPDX-License-Identifier: GPL-3.0-only
"""Generate the GitHub-readable guide from the illustrated HTML guide source."""
import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'img', 'br', 'hr', 'meta', 'link', 'input'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def descendants(node, tag):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == tag:
                yield child
            yield from descendants(child, tag)


def destination(url):
    if url.startswith('images/'):
        url = 'Adaptive Temperature Guide/' + url
    return quote(url, safe='/#%?:=&.-')


def inline(node):
    if isinstance(node, str):
        return re.sub(r'\s+', ' ', node)
    text = ''.join(inline(child) for child in node.children)
    if node.tag == 'strong':
        return '**' + text + '**'
    if node.tag == 'em':
        return '*' + text + '*'
    if node.tag == 'code':
        return '`' + text + '`'
    if node.tag == 'a':
        if any(isinstance(child, Node) and child.tag == 'img' for child in node.children):
            return text
        return '[' + text.strip() + '](' + destination(node.attrs.get('href', '')) + ')'
    if node.tag == 'img':
        alt = node.attrs.get('alt', '').replace('[', '\\[').replace(']', '\\]')
        return '![' + alt + '](' + destination(node.attrs['src']) + ')'
    if node.tag == 'br':
        return '<br>'
    return text


def block(node):
    if isinstance(node, str):
        return ''
    classes = set(node.attrs.get('class', '').split())
    if classes & {'footer', 'step', 'eyebrow'} or node.tag in {'style', 'script', 'nav', 'h1'}:
        return ''
    text = inline(node).strip()
    if node.tag in {'h2', 'h3'}:
        return '#' * int(node.tag[1]) + ' ' + text + '\n\n'
    if node.tag == 'p':
        return text + '\n\n'
    if node.tag == 'figure':
        picture = next(descendants(node, 'img'))
        caption = next(descendants(node, 'figcaption'), None)
        return inline(picture) + '\n\n' + ('*' + inline(caption).strip() + '*\n\n' if caption else '')
    if node.tag in {'ol', 'ul'}:
        items = [n for n in node.children if isinstance(n, Node) and n.tag == 'li']
        return ''.join((str(i) + '. ' if node.tag == 'ol' else '- ') + inline(item).strip() + '\n'
                       for i, item in enumerate(items, 1)) + '\n'
    if node.tag == 'table':
        rows = []
        for row in descendants(node, 'tr'):
            cells = [inline(n).strip().replace('|', '\\|') for n in row.children
                     if isinstance(n, Node) and n.tag in {'th', 'td'}]
            rows.append('| ' + ' | '.join(cells) + ' |')
        width = len([n for n in next(descendants(node, 'tr')).children
                     if isinstance(n, Node) and n.tag in {'th', 'td'}])
        return rows[0] + '\n| ' + ' | '.join(['---'] * width) + ' |\n' + '\n'.join(rows[1:]) + '\n\n'
    if classes & {'notice', 'note'}:
        return '> ' + text + '\n\n'
    if 'toc' in classes:
        return ''.join('- ' + inline(n).strip() + '\n' for n in node.children
                       if isinstance(n, Node) and n.tag == 'a') + '\n'
    return ''.join(block(child) for child in node.children)


def render(source):
    doc = Document()
    doc.feed(source)
    sections = [n for n in descendants(doc.root, 'section') if 'sheet' in n.attrs.get('class', '').split()]
    result = '# Adaptive Temperature user guide\n\n'
    version = re.search(r'Version ([0-9]+\.[0-9]+\.[0-9]+)', source)
    if version:
        result += 'Version **' + version[1] + '**.\n\n'
    result += '[Project home](../README.md) · [Download the PDF](Adaptive%20Temperature%20Guide/Adaptive%20Temperature%20User%20Guide.pdf?raw=1)\n\n'
    for section in sections:
        result += '<a name="' + section.attrs['id'] + '"></a>\n\n'
        result += block(section)
    return re.sub(r'\n{3,}', '\n\n', result).rstrip() + '\n'


def build(root=ROOT, check=False):
    source = (root / 'docs/guide-content.html').read_text(encoding='utf-8')
    target = root / 'docs/USER_GUIDE.md'
    content = render(source)
    if check:
        if not target.is_file() or target.read_text(encoding='utf-8') != content:
            raise SystemExit('docs/USER_GUIDE.md is stale. Run python tools/build_repo_docs.py.')
        print('GitHub guide matches the illustrated guide source.')
    else:
        target.write_text(content, encoding='utf-8')
        print('Generated docs/USER_GUIDE.md')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    build(check=args.check)
