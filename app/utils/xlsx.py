"""Helpers for preserving Excel features unsupported by openpyxl."""

import io
import re
import zipfile


_EXTENSION_LIST = re.compile(
    rb'<(?:[A-Za-z0-9_]+:)?extLst\b.*?</(?:[A-Za-z0-9_]+:)?extLst>',
    re.DOTALL,
)
_WORKSHEET_OPEN = re.compile(rb'<worksheet\b[^>]*>')
_PREFIX_NAMESPACE = re.compile(
    rb'\s(xmlns:[A-Za-z_][A-Za-z0-9_.-]*="[^"]*")'
)


def _self_contained_extension(worksheet_xml, extension):
    """Copy inherited prefix declarations onto a detached ``extLst`` block.

    Excel's extension block can use a prefix in an attribute (notably
    ``xr:uid``) whose namespace is declared only on the source worksheet
    element.  Once that block is copied into openpyxl's newly written
    worksheet, the declaration no longer exists and the XLSX contains
    malformed XML.  Keeping the declarations on the opaque block makes it
    safe to transplant without changing its contents.
    """
    worksheet_match = _WORKSHEET_OPEN.search(worksheet_xml)
    if not worksheet_match:
        return extension
    declarations = _PREFIX_NAMESPACE.findall(worksheet_match.group(0))
    if not declarations:
        return extension
    attributes = b' '.join(declarations)
    return extension.replace(b'<extLst>', b'<extLst ' + attributes + b'>', 1)


def restore_worksheet_extensions(base_path, output_buffer):
    """Restore worksheet ``extLst`` blocks stripped by openpyxl.

    SIT's Template 2 contains x14 data validation which openpyxl can read but
    cannot write. The application changes cell data only; copying those opaque
    worksheet extension blocks from the trusted base template preserves its
    Excel dropdown behaviour without interpreting or modifying the extension.
    """
    output_buffer.seek(0)
    output_bytes = output_buffer.read()

    with zipfile.ZipFile(base_path, 'r') as base_zip:
        source_extensions = {}
        for name in base_zip.namelist():
            if not name.startswith('xl/worksheets/') or not name.endswith('.xml'):
                continue
            worksheet_xml = base_zip.read(name)
            match = _EXTENSION_LIST.search(worksheet_xml)
            if match:
                source_extensions[name] = _self_contained_extension(
                    worksheet_xml, match.group(0)
                )

    rebuilt = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(output_bytes), 'r') as source_zip:
        with zipfile.ZipFile(rebuilt, 'w') as destination_zip:
            for info in source_zip.infolist():
                payload = source_zip.read(info.filename)
                extension = source_extensions.get(info.filename)
                if extension and b'x14:dataValidations' not in payload:
                    payload = payload.replace(
                        b'</worksheet>', extension + b'</worksheet>', 1
                    )
                destination_zip.writestr(info, payload)

    output_buffer.seek(0)
    output_buffer.truncate(0)
    output_buffer.write(rebuilt.getvalue())
    output_buffer.seek(0)
