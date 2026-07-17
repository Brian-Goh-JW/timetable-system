"""
STEP 47 — Replace every Professor.staff_id with a mock value.

Brian, 2026-07-17: "replace all of the staff id with mock id" - real SIT
staff IDs (e.g. 'A100909', 'G1003', 'R100030') were sourced directly from
Ms. Yang's own uploaded files and flow into the Template 2 export's SIS
Staff ID columns. Flagged as likely NDA/confidentiality-sensitive and
should not appear in an export used for demos/testing outside the real
system.

Scope: staff_id only (per Brian's explicit request) - names, emails, and
every other field are untouched. Assigns 'STAFF####' sequentially by
Professor.id, so the mapping is stable and reproducible if this script is
re-run (e.g. against a freshly re-seeded DB).

Downstream effect (documented, not blocking): future re-imports of Ms.
Yang's real source files match professors by staff_id first
(_get_or_create_professor in admin.py), falling back to a name match if
that fails. Since every mock ID is unique and won't match a real
uploaded staff_id, re-imports will fall through to the name-match path
instead of creating duplicates - and will NOT overwrite the mock ID back
to a real one (see the `if sid and not prof.staff_id` guard), so the
anonymisation survives future re-imports. The bulk "Import Professors"
feature has an independent email-based fallback match, unaffected either
way since emails aren't touched here.

Run:
    python bootstrap/47_mock_professor_staff_ids.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.stdout.reconfigure(encoding='utf-8')

from app import create_app, db
from app.models.professor import Professor

app = create_app()

with app.app_context():
    profs = Professor.query.order_by(Professor.id).all()
    print(f'Found {len(profs)} professor(s).')

    changed = 0
    for i, p in enumerate(profs, start=1):
        new_id = f'STAFF{i:04d}'
        if p.staff_id != new_id:
            p.staff_id = new_id
            changed += 1

    db.session.commit()
    print(f'Assigned mock staff IDs to {changed} professor(s) (STAFF0001..STAFF{len(profs):04d}).')

    # Sanity check: uniqueness held, every professor has a value
    all_ids = [p.staff_id for p in Professor.query.all()]
    assert len(all_ids) == len(set(all_ids)), 'Duplicate staff_id after mock assignment!'
    assert all(all_ids), 'Blank staff_id after mock assignment!'
    print('Uniqueness check passed.')
