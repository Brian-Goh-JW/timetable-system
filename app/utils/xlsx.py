"""Helpers for preserving Excel features unsupported by openpyxl."""

import io
import re
import zipfile


_EXTENSION_LIST = re.compile(
    rb'<(?:[A-Za-z0-9_]+:)?extLst\b.*?</(?:[A-Za-z0-9_]+:)?extLst>',
    re.DOTALL,
)


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
            match = _EXTENSION_LIST.search(base_zip.read(name))
            if match:
                source_extensions[name] = match.group(0)

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
