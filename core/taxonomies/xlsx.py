"""Small read-only XLSX iterator using only the Python standard library."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _column_index(reference):
    letters = re.match(r'[A-Z]+', reference or '')
    value = 0
    for char in (letters.group(0) if letters else 'A'):
        value = value * 26 + ord(char) - 64
    return value - 1


def workbook_sheets(path):
    with ZipFile(Path(path)) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {
            row.attrib['Id']: row.attrib['Target']
            for row in rels.findall(f'{{{PKG_REL}}}Relationship')
        }
        return {
            sheet.attrib['name']: 'xl/' + targets[sheet.attrib[f'{{{REL}}}id']].lstrip('/')
            for sheet in workbook.findall(f'.//{{{MAIN}}}sheet')
        }


def iter_rows(path, sheet_name=None):
    path = Path(path)
    with ZipFile(path) as archive:
        sheets = workbook_sheets(path)
        if sheet_name is None:
            sheet_name = next(iter(sheets))
        if sheet_name not in sheets:
            raise ValueError(f'Workbook has no sheet {sheet_name!r}; available: {list(sheets)}')
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [
                ''.join(node.text or '' for node in item.findall(f'.//{{{MAIN}}}t'))
                for item in root.findall(f'{{{MAIN}}}si')
            ]
        root = ET.fromstring(archive.read(sheets[sheet_name]))
        for row in root.findall(f'.//{{{MAIN}}}row'):
            values = []
            for cell in row.findall(f'{{{MAIN}}}c'):
                index = _column_index(cell.attrib.get('r'))
                while len(values) <= index:
                    values.append('')
                kind = cell.attrib.get('t')
                value_node = cell.find(f'{{{MAIN}}}v')
                if kind == 'inlineStr':
                    value = ''.join(
                        node.text or '' for node in cell.findall(f'.//{{{MAIN}}}t'))
                elif value_node is None:
                    value = ''
                elif kind == 's':
                    value = shared[int(value_node.text)]
                else:
                    value = value_node.text or ''
                values[index] = value
            yield values
