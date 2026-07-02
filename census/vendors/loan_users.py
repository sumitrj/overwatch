"""
Associator: the loan-customer database.

An associator is a data vendor that resolves an admitted identifier (here, an email) against
a data source and, on a hit, promotes an anonymous sensed visitor into a KNOWN person — the
moment the data layer starts empowering the intelligence layer. A real deployment plugs a CRM
/ core-banking lookup in behind this same `match()` seam; the demo ships a small seed table.
"""
from __future__ import annotations

SEED_RECORDS: dict[str, dict] = {
    "priya@example.com": {"customer_id": "LN-4471", "name": "Priya S.",
                          "product": "Home Loan", "stage": "pre-approved"},
    "arjun@example.com": {"customer_id": "LN-2290", "name": "Arjun M.",
                          "product": "Personal Loan", "stage": "applicant"},
    "meera@example.com": {"customer_id": "LN-8830", "name": "Meera K.",
                          "product": "Vehicle Loan", "stage": "disbursed"},
}


class LoanUsersVendor:
    name = "loan_users"
    label = "Loan Users database"

    def __init__(self, records: dict | None = None) -> None:
        self.records = records if records is not None else dict(SEED_RECORDS)

    def match(self, identifiers: dict) -> dict | None:
        """Resolve on email; returns the customer record or None."""
        email = identifiers.get("email")
        return dict(self.records[email]) if email in self.records else None
