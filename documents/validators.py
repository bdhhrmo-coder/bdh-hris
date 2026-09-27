"""
Upload validation — CLAUDE.md §10: PDF/JPG/JPEG/PNG only, max 10MB, max 5
files per transaction, and reject unsafe/disallowed formats. Extension
alone isn't trusted (a renamed file would sail through) — the first bytes
are checked against each format's real signature.
"""

from django.core.exceptions import ValidationError

from .models import UploadedDocument

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILES_PER_TRANSACTION = 5

EXTENSION_TO_FILE_TYPE = {
    "pdf": UploadedDocument.PDF,
    "jpg": UploadedDocument.JPG,
    "jpeg": UploadedDocument.JPG,
    "png": UploadedDocument.PNG,
}

# Real file signatures ("magic bytes"), checked regardless of what the
# extension claims.
SIGNATURES = {
    UploadedDocument.PDF: (b"%PDF-",),
    UploadedDocument.JPG: (b"\xff\xd8\xff",),
    UploadedDocument.PNG: (b"\x89PNG\r\n\x1a\n",),
}


def _extension_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def validate_upload(django_file, existing_active_count):
    """
    Raises ValidationError on any violation; otherwise returns the
    detected file_type (UploadedDocument.PDF/JPG/PNG) for the caller to
    store. `existing_active_count` is the number of already-active
    documents on the target transaction, checked against
    MAX_FILES_PER_TRANSACTION before adding one more.
    """
    if existing_active_count >= MAX_FILES_PER_TRANSACTION:
        raise ValidationError(f"This transaction already has {MAX_FILES_PER_TRANSACTION} document(s) attached.")

    if django_file.size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(f"File is too large — maximum is {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB.")

    ext = _extension_of(django_file.name)
    file_type = EXTENSION_TO_FILE_TYPE.get(ext)
    if file_type is None:
        raise ValidationError("Only PDF, JPG/JPEG, or PNG files are accepted.")

    django_file.seek(0)
    header = django_file.read(16)
    django_file.seek(0)
    if not any(header.startswith(sig) for sig in SIGNATURES[file_type]):
        raise ValidationError(
            "This file's content doesn't match its extension — it may be corrupted, mislabeled, or "
            "an unsafe/disallowed format."
        )

    return file_type
