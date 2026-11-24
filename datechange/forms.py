from django import forms

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class DateChangeForm(forms.Form):
    new_start_date = forms.DateField(widget=DATE, label="New start date")
    new_end_date = forms.DateField(widget=DATE, label="New end date")
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Justification",
                                    help_text="Why the dates need to move. Required.")

    def clean_justification(self):
        text = self.cleaned_data["justification"].strip()
        if not text:
            raise forms.ValidationError("A justification is required.")
        return text
