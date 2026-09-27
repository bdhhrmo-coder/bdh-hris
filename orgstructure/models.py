from django.db import models


class Section(models.Model):
    """
    A hospital section (per CLAUDE.md Section 4). Fixed list in practice,
    but kept admin-configurable rather than hardcoded, since BDH may add
    or rename sections over time.
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Unit(models.Model):
    """
    A unit within a section (e.g. Nursing Service Section -> OPD, ER, ...).
    Most sections have no sub-units; only Nursing Service currently does,
    but the model supports any section having units.
    """

    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="units")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["section__name", "name"]
        unique_together = ("section", "name")

    def __str__(self):
        return f"{self.section.name} — {self.name}"
