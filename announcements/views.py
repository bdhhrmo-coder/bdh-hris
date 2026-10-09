from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import RoleAssignment
from employees.permissions import get_acting_employee

from . import services
from .forms import AnnouncementForm
from .models import Announcement

HOMEPAGE_LIMIT = 5


def can_manage(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)


def _require_manager(request):
    acting = get_acting_employee(request.user)
    if not can_manage(acting):
        raise PermissionDenied("Only the HR Administrator manages announcements.")
    return acting


def _type_filter(request):
    t = request.GET.get("type", "")
    return t if t in dict(Announcement.TYPE_CHOICES) else ""


def homepage_panel(request, acting):
    """Context for the homepage panel: newest first, filterable by type."""
    services.archive_expired()
    t = _type_filter(request)
    qs = Announcement.visible_to(acting)
    if t:
        qs = qs.filter(announcement_type=t)
    return {"announcements_panel_template": "announcements/_panel.html",
            "announcements": qs[:HOMEPAGE_LIMIT], "announcement_type": t,
            "announcement_types": Announcement.TYPE_CHOICES}


@login_required
def announcement_list(request):
    acting = get_acting_employee(request.user)
    services.archive_expired()
    t = _type_filter(request)
    archive_tab = request.GET.get("tab") == "archive"
    qs = Announcement.visible_to(acting, Announcement.ARCHIVED if archive_tab else None)
    if t:
        qs = qs.filter(announcement_type=t)
    return render(request, "announcements/list.html", {
        "announcements": qs, "announcement_type": t, "announcement_types": Announcement.TYPE_CHOICES,
        "can_manage": can_manage(acting), "archive_tab": archive_tab,
    })


def _viewable(request, pk):
    acting = get_acting_employee(request.user)
    a = get_object_or_404(Announcement, pk=pk)
    if not (can_manage(acting) or a.is_visible_to(acting)):
        raise Http404("No such announcement.")
    return acting, a


@login_required
def announcement_detail(request, pk):
    acting, a = _viewable(request, pk)
    return render(request, "announcements/detail.html", {
        "a": a, "can_manage": can_manage(acting), "history": a.actions.select_related("acted_by__employee"),
    })


@login_required
def announcement_pdf(request, pk):
    acting, a = _viewable(request, pk)
    if not a.pdf:
        raise Http404("No signed copy attached.")
    return FileResponse(a.pdf.open("rb"), filename=a.pdf_original_name or f"announcement-{a.pk}.pdf")


@login_required
def manage(request):
    _require_manager(request)
    services.archive_expired()
    return render(request, "announcements/manage.html", {"announcements": Announcement.objects.all()})


def _save(request, form, created):
    a = form.save(commit=False)
    if created:
        a.created_by = request.user
    upload = request.FILES.get("pdf")
    if upload:
        a.pdf_original_name = upload.name[:255]
    a.save()
    form.save_m2m()
    changed = ", ".join(form.changed_data) or "no field changes"
    services.log(a, "create" if created else "edit", request.user, "" if created else f"Changed: {changed}")
    return a


@login_required
def create(request):
    _require_manager(request)
    form = AnnouncementForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        a = _save(request, form, created=True)
        messages.success(request, "Announcement saved as a draft. Publish it when it is ready.")
        return redirect("announcements:detail", pk=a.pk)
    return render(request, "announcements/form.html", {"form": form})


@login_required
def edit(request, pk):
    _require_manager(request)
    a = get_object_or_404(Announcement, pk=pk)
    if a.status != Announcement.DRAFT:
        messages.error(request, "Only a draft can be edited. Archive this one and post a new announcement instead.")
        return redirect("announcements:detail", pk=pk)
    form = AnnouncementForm(request.POST or None, request.FILES or None, instance=a)
    if request.method == "POST" and form.is_valid():
        _save(request, form, created=False)
        messages.success(request, "Draft updated.")
        return redirect("announcements:detail", pk=pk)
    return render(request, "announcements/form.html", {"form": form, "a": a})


@login_required
@require_POST
def publish(request, pk):
    _require_manager(request)
    a = get_object_or_404(Announcement, pk=pk, status=Announcement.DRAFT)
    services.publish(a, request.user)
    messages.success(request, "Announcement published. The staff who can see it were notified.")
    return redirect("announcements:detail", pk=pk)


@login_required
@require_POST
def archive(request, pk):
    _require_manager(request)
    a = get_object_or_404(Announcement, pk=pk, status=Announcement.PUBLISHED)
    services.archive(a, request.user)
    messages.success(request, "Announcement archived.")
    return redirect("announcements:detail", pk=pk)
