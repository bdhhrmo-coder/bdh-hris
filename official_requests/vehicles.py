"""Type of Transport for a Local Official Errand (F59, owner request 2026-11-03).

Kept apart from f59.py so the request forms do not need the Word library
(python-docx) that only the F59 printout uses (fix 2026-11-24).
"""

AMBULANCE = "Ambulance"
SERVICE_VEHICLE = "Service Vehicle"
VEHICLE_CHOICES = [("", "None (walking / own means)"), (AMBULANCE, AMBULANCE), (SERVICE_VEHICLE, SERVICE_VEHICLE)]
