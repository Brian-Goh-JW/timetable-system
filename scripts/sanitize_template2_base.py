"""Remove reference-organisation data from the tracked Template 2 workbook.

The workbook is a formatting/validation template, not a data source.  Keep its
headers, styles, lookup structures, relationships, and Excel extension blocks,
but clear all timetable, staff, course, and location data rows.  Unreferenced
shared strings are rebuilt so removed names and identifiers do not remain
hidden inside the XLSX package.
"""

from __future__ import annotations

import argparse
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
SENSITIVE_SHEETS = {
    'Timetable',
    'Course Code',
    'Location',
    'Staff',
    'Sheet4',
    'Sheet1',
    'Sheet2',
    'Sheet3',
}
SHEET_DATA = re.compile(rb'(<sheetData>)(.*?)(</sheetData>)', re.DOTALL)
ROW_ONE = re.compile(rb'<row\b[^>]*\br="1"[^>]*>.*?</row>', re.DOTALL)
SHARED_CELL = re.compile(
    rb'(<c\b[^>]*\bt="s"[^>]*>.*?<v>)(\d+)(</v>.*?</c>)',
    re.DOTALL,
)


def _sheet_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    namespace = {'m': MAIN_NS, 'r': REL_NS}
    workbook = ElementTree.fromstring(archive.read('xl/workbook.xml'))
    relationships = ElementTree.fromstring(
        archive.read('xl/_rels/workbook.xml.rels')
    )
    rel_targets = {
        relation.attrib['Id']: relation.attrib['Target']
        for relation in relationships
    }
    targets = {}
    for sheet in workbook.findall('.//m:sheet', namespace):
        relation_id = sheet.attrib[f'{{{REL_NS}}}id']
        target = rel_targets[relation_id].lstrip('/')
        if not target.startswith('xl/'):
            target = 'xl/' + target
        targets[sheet.attrib['name']] = target
    return targets


def _header_only(worksheet_xml: bytes) -> bytes:
    def replace_sheet_data(match: re.Match[bytes]) -> bytes:
        row = ROW_ONE.search(match.group(2))
        return match.group(1) + (row.group(0) if row else b'') + match.group(3)

    return SHEET_DATA.sub(replace_sheet_data, worksheet_xml, count=1)


def sanitize(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source, 'r') as archive:
        targets = _sheet_targets(archive)
        payloads = {
            item.filename: archive.read(item.filename)
            for item in archive.infolist()
        }
        infos = archive.infolist()

    for sheet_name in SENSITIVE_SHEETS:
        target = targets.get(sheet_name)
        if target and target in payloads:
            payloads[target] = _header_only(payloads[target])

    worksheet_names = [
        name for name in payloads
        if name.startswith('xl/worksheets/') and name.endswith('.xml')
    ]
    referenced_indices = []
    for name in worksheet_names:
        referenced_indices.extend(
            int(match.group(2))
            for match in SHARED_CELL.finditer(payloads[name])
        )
    retained = sorted(set(referenced_indices))
    old_to_new = {old: new for new, old in enumerate(retained)}

    shared_name = 'xl/sharedStrings.xml'
    shared_root = ElementTree.fromstring(payloads[shared_name])
    shared_items = shared_root.findall(f'{{{MAIN_NS}}}si')
    new_root = ElementTree.Element(
        f'{{{MAIN_NS}}}sst',
        {
            'count': str(len(referenced_indices)),
            'uniqueCount': str(len(retained)),
        },
    )
    for old_index in retained:
        new_root.append(shared_items[old_index])
    ElementTree.register_namespace('', MAIN_NS)
    payloads[shared_name] = ElementTree.tostring(
        new_root, encoding='utf-8', xml_declaration=True
    )

    def remap_shared_cell(match: re.Match[bytes]) -> bytes:
        return (
            match.group(1)
            + str(old_to_new[int(match.group(2))]).encode('ascii')
            + match.group(3)
        )

    for name in worksheet_names:
        payloads[name] = SHARED_CELL.sub(remap_shared_cell, payloads[name])

    rebuilt = io.BytesIO()
    with zipfile.ZipFile(rebuilt, 'w') as output:
        for info in infos:
            output.writestr(info, payloads[info.filename])
    destination.write_bytes(rebuilt.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    destination = args.output or args.workbook
    sanitize(args.workbook, destination)


if __name__ == '__main__':
    main()
