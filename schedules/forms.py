from datetime import date

from django import forms

from .models import ShiftCode

MONTHS = [(i, date(2000, i, 1).strftime("%B")) for i in range(1, 13)]


class NewScheduleForm(forms.Form):
    area = forms.ChoiceField(label="Section or unit")
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int)
    year = forms.IntegerField(min_value=2024, max_value=2100)

    def __init__(self, *args, sections=(), units=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._sections = {str(s.pk): s for s in sections}
        self._units = {str(u.pk): u for u in units}
        self.fields["area"].choices = (
            [("", "— choose —")]
            + [(f"s{s.pk}", s.name) for s in sections]
            + [(f"u{u.pk}", f"{u.section.name} — {u.name}") for u in units]
        )
        nxt = date.today().replace(day=28)
        nxt = date(nxt.year + (nxt.month == 12), nxt.month % 12 + 1, 1)
        self.fields["month"].initial = nxt.month
        self.fields["year"].initial = nxt.year

    def chosen_area(self):
        value = self.cleaned_data["area"]
        if value.startswith("u"):
            return None, self._units[value[1:]]
        return self._sections[value[1:]], None


class ShiftCodeForm(forms.ModelForm):
    class Meta:
        model = ShiftCode
        fields = ["code", "description", "start_time", "end_time", "paid_hours", "is_duty", "is_active", "sort_order"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}),
                   "end_time": forms.TimeInput(attrs={"type": "time"})}

    def clean(self):
        c = super().clean()
        if c.get("is_duty") and not c.get("paid_hours"):
            self.add_error("paid_hours", "A duty shift needs its paid hours.")
        return c
