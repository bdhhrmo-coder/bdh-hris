"""Biometric ID field (owner request 2026-11-03): optional, unique when filled."""

from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Employee


def emp(eid, bio=""):
    return Employee(user=User.objects.create_user(eid.lower(), password="x"), employee_id=eid, surname="S",
                    first_name="F", date_hired=date(2024, 1, 1), biometric_id=bio)


class BiometricIdTests(TestCase):
    def test_blank_is_allowed_for_many_and_filled_must_be_unique(self):
        emp("B1").save()
        emp("B2").save()                       # two blanks are fine
        emp("B3", "0042").save()
        dup = emp("B4", "0042")
        with self.assertRaises(ValidationError):
            dup.full_clean()
