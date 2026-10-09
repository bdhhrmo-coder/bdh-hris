from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

from documents.models import UploadedDocument
from documents.validators import validate_upload
from orgstructure.models import Section, Unit

from .models import Announcement


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["announcement_type", "title", "reference_no", "date_issued", "effective_date", "summary",
                  "expiry_date", "pdf", "visibility", "sections", "units"]
        widgets = {
            "date_issued": forms.DateInput(attrs={"type": "date"}),
            "effective_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
            "summary": forms.Textarea(attrs={"rows": 4}),
            "sections": forms.CheckboxSelectMultiple,
            "units": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sections"].queryset = Section.objects.filter(is_active=True).order_by("name")
        self.fields["units"].queryset = Unit.objects.filter(is_active=True).order_by("name")
        self.fields["pdf"].widget.attrs["accept"] = ".pdf,application/pdf"

    def clean_pdf(self):
        f = self.cleaned_data.get("pdf")
        if isinstance(f, UploadedFile):
            # Same rules as every other upload (CLAUDE.md §10), PDF only here.
            file_type = validate_upload(f, existing_active_count=0)
            if file_type != UploadedDocument.PDF:
                raise ValidationError("Only a PDF of the signed copy is accepted here.")
        return f

    def clean(self):
        c = super().clean()
        if c.get("effective_date") and c.get("date_issued") and c["effective_date"] < c["date_issued"]:
            self.add_error("effective_date", "The effective date can't be before the date issued.")
        if c.get("expiry_date") and c.get("effective_date") and c["expiry_date"] < c["effective_date"]:
            self.add_error("expiry_date", "The expiry date can't be before the effective date.")
        if c.get("visibility") == Announcement.SELECTED and not (c.get("sections") or c.get("units")):
            self.add_error("visibility", "Choose at least one section or unit, or make it visible to all staff.")
        return c
