from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from .forms import DocumentUploadForm
from .models import UploadedDocument, UploadedDocumentEvent
from .permissions import can_upload_to, can_view_document, can_view_transaction, get_acting_employee
from .requirements import required_documents_for, requirement_status_for
from .validators import validate_upload


def _get_target(app_label, model_name, object_id):
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        raise Http404("Unknown document target.")
    content_type = ContentType.objects.get_for_model(model)
    obj = get_object_or_404(model, pk=object_id)
    return content_type, obj


@login_required
def document_list(request, app_label, model_name, object_id):
    acting_employee = get_acting_employee(request.user)
    content_type, obj = _get_target(app_label, model_name, object_id)

    if not can_view_transaction(acting_employee, obj):
        raise PermissionDenied("You are not authorized to view this transaction's documents.")

    all_documents = UploadedDocument.objects.filter(
        content_type=content_type, object_id=obj.pk, is_active=True
    ).select_related("requirement")
    visible_documents = [d for d in all_documents if can_view_document(acting_employee, d)]
    hidden_confidential_count = len(all_documents) - len(visible_documents)

    return render(request, "documents/document_list.html", {
        "obj": obj,
        "app_label": app_label,
        "model_name": model_name,
        "documents": visible_documents,
        "hidden_confidential_count": hidden_confidential_count,
        "requirement_status": requirement_status_for(obj),
        "can_upload": can_upload_to(acting_employee, obj),
    })


@login_required
def document_upload(request, app_label, model_name, object_id):
    acting_employee = get_acting_employee(request.user)
    content_type, obj = _get_target(app_label, model_name, object_id)

    if not can_upload_to(acting_employee, obj):
        raise PermissionDenied("You are not authorized to attach documents to this transaction.")

    existing_count = UploadedDocument.objects.filter(content_type=content_type, object_id=obj.pk, is_active=True).count()
    requirement_queryset = required_documents_for(obj)

    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, requirement_queryset=requirement_queryset)
        if form.is_valid():
            try:
                file_type = validate_upload(form.cleaned_data["file"], existing_count)
            except ValidationError as exc:
                form.add_error("file", exc.message)
            else:
                requirement = form.cleaned_data.get("requirement")
                document = UploadedDocument.objects.create(
                    content_type=content_type,
                    object_id=obj.pk,
                    requirement=requirement,
                    file=form.cleaned_data["file"],
                    original_filename=form.cleaned_data["file"].name,
                    file_size=form.cleaned_data["file"].size,
                    file_type=file_type,
                    is_confidential=(requirement.is_confidential if requirement else form.cleaned_data["mark_confidential"]),
                    uploaded_by=request.user,
                )
                UploadedDocumentEvent.objects.create(
                    document=document, event_type=UploadedDocumentEvent.UPLOADED, acted_by=request.user,
                )
                messages.success(request, f"Uploaded {document.original_filename}.")
                return redirect("documents:document_list", app_label=app_label, model_name=model_name, object_id=object_id)
    else:
        form = DocumentUploadForm(requirement_queryset=requirement_queryset)

    return render(request, "documents/document_upload.html", {
        "form": form, "obj": obj, "app_label": app_label, "model_name": model_name,
    })


@login_required
def document_replace(request, pk):
    acting_employee = get_acting_employee(request.user)
    old_document = get_object_or_404(UploadedDocument, pk=pk)
    obj = old_document.content_object

    if obj is None or not can_upload_to(acting_employee, obj):
        raise PermissionDenied("You are not authorized to replace this document.")
    if not old_document.is_active:
        raise PermissionDenied("This document has already been replaced.")

    app_label = old_document.content_type.app_label
    model_name = old_document.content_type.model
    requirement_queryset = required_documents_for(obj)
    existing_count = UploadedDocument.objects.filter(
        content_type=old_document.content_type, object_id=old_document.object_id, is_active=True
    ).count() - 1  # the document being replaced won't count once superseded

    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, requirement_queryset=requirement_queryset)
        if form.is_valid():
            try:
                file_type = validate_upload(form.cleaned_data["file"], existing_count)
            except ValidationError as exc:
                form.add_error("file", exc.message)
            else:
                requirement = form.cleaned_data.get("requirement") or old_document.requirement
                new_document = UploadedDocument.objects.create(
                    content_type=old_document.content_type,
                    object_id=old_document.object_id,
                    requirement=requirement,
                    file=form.cleaned_data["file"],
                    original_filename=form.cleaned_data["file"].name,
                    file_size=form.cleaned_data["file"].size,
                    file_type=file_type,
                    is_confidential=old_document.is_confidential or form.cleaned_data["mark_confidential"],
                    uploaded_by=request.user,
                )
                old_document.is_active = False
                old_document.superseded_by = new_document
                old_document.save(update_fields=["is_active", "superseded_by"])
                UploadedDocumentEvent.objects.create(
                    document=new_document, event_type=UploadedDocumentEvent.REPLACED,
                    replaces=old_document, acted_by=request.user,
                )
                messages.success(request, f"Replaced with {new_document.original_filename}.")
                return redirect(
                    "documents:document_list", app_label=app_label, model_name=model_name, object_id=old_document.object_id
                )
    else:
        form = DocumentUploadForm(requirement_queryset=requirement_queryset)

    return render(request, "documents/document_replace.html", {"form": form, "old_document": old_document})


@login_required
def document_download(request, pk):
    acting_employee = get_acting_employee(request.user)
    document = get_object_or_404(UploadedDocument, pk=pk)

    if not can_view_document(acting_employee, document):
        raise PermissionDenied("You are not authorized to view this document.")

    return FileResponse(document.file.open("rb"), filename=document.original_filename)
