from django import forms

from .models import DocumentRequirement


class DocumentUploadForm(forms.Form):
    file = forms.FileField()
    requirement = forms.ModelChoiceField(queryset=DocumentRequirement.objects.none(), required=False)
    mark_confidential = forms.BooleanField(
        required=False, help_text="Check if this is a medical certificate, government ID, or similarly sensitive.",
    )

    def __init__(self, *args, requirement_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if requirement_queryset is not None:
            self.fields["requirement"].queryset = requirement_queryset
        # Staff only need the document's name ("Allowed to Work form"), not
        # the model/sub-type prefix that DocumentRequirement.__str__ adds for
        # the admin screens.
        self.fields["requirement"].label_from_instance = lambda req: req.label
