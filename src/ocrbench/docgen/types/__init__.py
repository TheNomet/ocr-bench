"""One module per document type; each exposes ``SCHEMA`` and ``build(r) -> Doc``."""

from . import bank_statement, holiday_survey, invoice, letter, newsletter, payslip, receipt, rental_contract

MODULES = {
    "letter": letter,
    "receipt": receipt,
    "bank_statement": bank_statement,
    "holiday_survey": holiday_survey,
    "invoice": invoice,
    "payslip": payslip,
    "rental_contract": rental_contract,
    "newsletter": newsletter,
}
