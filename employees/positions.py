"""Position records and Exchange groups (owner decisions 2026-11-26).

Position titles are still typed on the employee record (and in the Data
Import). Every save links the employee to the Position record for that title,
created on first use; matching ignores case and extra spaces, so "NURSE I"
and "Nurse I " are one record, while "Clinical Nurse" is another.

The HR Administrator gives a position an Exchange group on HR Administration
-> Positions and Exchange Groups. Exchange of Duty partners
(exchange/schedule_checks.py) must then be in the same group:
  - both positions in the same group  -> allowed
  - different groups                  -> blocked
  - one in a group, the other in none -> blocked
  - neither in a group                -> allowed (same-section rule only)
"""


def title_key(title):
    return " ".join((title or "").split()).lower()


def position_for(title):
    """The Position record for this title (created if new); None if blank."""
    from .models import Position

    key = title_key(title)
    if not key:
        return None
    pos, _ = Position.objects.get_or_create(title_key=key, defaults={"title": " ".join(title.split())})
    return pos


def group_of(employee):
    pos = employee.position_ref
    return pos.exchange_group if pos is not None else None


def group_error(employee_a, employee_b):
    """None if the two positions may exchange duty, else the message."""
    ga, gb = group_of(employee_a), group_of(employee_b)
    if ga is None and gb is None:
        return None
    if ga is not None and gb is not None and ga.pk == gb.pk:
        return None

    def label(e, g):
        title = e.position or "no position on record"
        return f"{e} ({title}: {g.name + ' group' if g else 'no Exchange group'})"

    return (f"Exchange partners must hold positions in the same Exchange group. {label(employee_a, ga)} and "
            f"{label(employee_b, gb)} are not.")


def set_group(position, group, user):
    """Change one position's Exchange group; logged for the Audit Log."""
    from .models import PositionGroupChange

    old = position.exchange_group
    if (old.pk if old else None) == (group.pk if group else None):
        return False
    position.exchange_group = group
    position.save(update_fields=["exchange_group"])
    PositionGroupChange.objects.create(position=position, old_group=old.name if old else "",
                                       new_group=group.name if group else "", changed_by=user)
    return True
