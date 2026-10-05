from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Section, Unit

EXPECTED_SECTIONS = {
    "Medical Services Section",
    "Nursing Service Section",
    "Radiology Section",
    "Laboratory Section",
    "Pharmacy Section",
    "Health Information Management Section",
    "Medical Social Service Section",
    "Accounting and Finance Section",
    "Human Resources Management Section",
    "General Services Section",
    "Procurement, Property and Supply Section",
    "Dietary Section",
}


class SeededOrgStructureTests(TestCase):
    """The starting org structure must match CLAUDE.md Section 4."""

    def test_all_twelve_sections_are_seeded(self):
        self.assertEqual(set(Section.objects.values_list("name", flat=True)), EXPECTED_SECTIONS)

    def test_nursing_service_has_exactly_its_six_units(self):
        nursing = Section.objects.get(name="Nursing Service Section")
        self.assertEqual(
            set(nursing.units.values_list("name", flat=True)),
            {"OPD", "ER", "Isolation/Medical", "Pediatric", "OB/Surgical", "OR/DR"},
        )

    def test_no_other_section_has_units(self):
        self.assertEqual(
            Unit.objects.exclude(section__name="Nursing Service Section").count(), 0
        )


class OrgStructureRulesTests(TestCase):
    def test_section_names_are_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Section.objects.create(name="Pharmacy Section")

    def test_unit_names_are_unique_within_a_section_only(self):
        pharmacy = Section.objects.get(name="Pharmacy Section")
        Unit.objects.create(section=pharmacy, name="ER")  # same name as a Nursing unit: allowed
        with self.assertRaises(IntegrityError), transaction.atomic():
            Unit.objects.create(section=pharmacy, name="ER")

    def test_string_forms(self):
        nursing = Section.objects.get(name="Nursing Service Section")
        self.assertEqual(str(nursing), "Nursing Service Section")
        self.assertEqual(str(nursing.units.get(name="ER")), "Nursing Service Section — ER")
