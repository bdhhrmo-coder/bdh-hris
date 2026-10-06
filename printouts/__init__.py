"""
Printable forms shared by several modules (leave, CTO, exchange of duty,
attendance correction).

BDH's direction (2026-10-06): the HRIS is meant to be paperless. Printing
is only incidental, on request. So instead of "signature over printed
name" lines to be signed by hand, each routing step prints a stamp that
says it was digitally processed/approved in the HRIS, with the person's
name, the date and the time - taken from the request's action log (the
same accountability record the queue screens use).

  stamps.py   - works out, from a request's action log, which steps are
                done, pending, returned or rejected (pure logic, unit-tested)
  sheet.py    - draws the letterhead, sections and stamp boxes into an
                openpyxl worksheet; PDF conversion is leave/pdf_convert.py

Form codes (owner-assigned, 2026-10-06):
  BDH-ADM-HR-01F04-A  COSP Leave application (leave/cosp_leave_form.py)
  BDH-ADM-HR-01F04-B  CTO application (cto/cto_form.py)
  BDH-ADM-HR-01F04-C  Exchange of Duty application (exchange/exchange_form.py)
  BDH-ADM-AO-01F10    Missed Log Justification Form, Revision 2
                      (attendance/correction_form.py)
CSC Form No. 6 (regular leave) is an official CSC form and is left as is:
no BDH code and no stamp, because regular leave is approved outside BDH.
"""
