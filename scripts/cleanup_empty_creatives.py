"""
One-time cleanup: delete Firestore ad_creatives documents that have no
media, headline, or ad text (all three fields are 'N/A').

Usage:
    python scripts/cleanup_empty_creatives.py [--dry-run]
"""

import sys
import firebase_admin
from firebase_admin import firestore

DRY_RUN = '--dry-run' in sys.argv
COLLECTION = 'ad_creatives'
BATCH_SIZE = 500

if not firebase_admin._apps:
    firebase_admin.initialize_app()

db = firestore.client()


def is_empty(doc: dict) -> bool:
    return (
        doc.get('headline', 'N/A') == 'N/A'
        and doc.get('ad_text', 'N/A') == 'N/A'
        and doc.get('firebase_storage_url', 'N/A') == 'N/A'
    )


def main():
    print(f"[INFO] Scanning {COLLECTION}... (dry_run={DRY_RUN})")
    docs = db.collection(COLLECTION).stream()

    to_delete = []
    for doc in docs:
        if is_empty(doc.to_dict()):
            to_delete.append(doc.reference)

    print(f"[INFO] Found {len(to_delete)} empty documents.")

    if not to_delete or DRY_RUN:
        if DRY_RUN:
            print("[DRY RUN] No changes made.")
        return

    deleted = 0
    batch = db.batch()
    for i, ref in enumerate(to_delete):
        batch.delete(ref)
        if (i + 1) % BATCH_SIZE == 0:
            batch.commit()
            batch = db.batch()
            print(f"[INFO] Deleted {i + 1} / {len(to_delete)}...")
    batch.commit()
    deleted = len(to_delete)

    print(f"[INFO] Done. Deleted {deleted} documents.")


if __name__ == '__main__':
    main()
