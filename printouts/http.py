"""One way for every print view to answer: the PDF, or - if LibreOffice
is missing or fails - a plain message instead of a bare "Server Error"."""

import logging

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


def pdf_response(request, render, filename, back_url_name):
    """`render` is a no-argument callable returning PDF bytes."""
    try:
        pdf_bytes = render()
    except RuntimeError as exc:
        logger.error("PDF conversion failed for %s: %s", filename, exc)
        messages.error(
            request,
            "The printable form could not be generated right now. Please contact your System "
            "Administrator (the server's PDF converter may not be installed).",
        )
        return redirect(back_url_name)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
