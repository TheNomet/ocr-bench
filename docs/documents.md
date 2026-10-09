# Synthetic documents

`ocrbench docs` (or `python -m ocrbench.docgen`) renders eight types of everyday Norwegian
(bokmål) paperwork. Names, addresses, amounts and account numbers are random and fictional;
output is deterministic for a given `seed`. Each page exists **clean** (a straight render)
and **degraded** (tilted, downscaled, blurred, noisy, warm-tinted, low-quality JPEG, to
imitate a phone photo).

The defaults (`per_type: 6`) give 48 documents and 54 pages, so 108 page images. The rental
contract runs to two pages. Ground truth per page is text in reading order plus the numeric
strings on the page; per document it is structured fields.

Where the layouts deliberately get hard:
- **receipt**: narrow monospace column, label and amount on opposite sides;
- **bank_statement**: dense 15–26 row table;
- **holiday_survey**: boxed key/value block filled in "handwriting" fonts, plus checkboxes;
- **newsletter**: two columns with an inline table;
- **rental_contract**: two pages of dense clauses.

## Types and fields

### `letter`

Personal letter written by a private person.

| field | type | description |
|---|---|---|
| `sender_name` | string | Full name of the person who wrote the letter |
| `date` | string | Date the letter was written (dd.mm.yyyy) |
| `recipient` | string (optional) | Name of the addressee in the recipient block |
| `amounts` | list | All money amounts mentioned in the letter, as printed |
| `phone` | string | Sender's phone number |

### `receipt`

Shop receipt (kvittering) from a store.

| field | type | description |
|---|---|---|
| `merchant` | string | Shop name at the top of the receipt |
| `date` | string | Purchase date (dd.mm.yyyy) |
| `time` | string | Purchase time (hh:mm) |
| `total` | string | Total amount paid (TOTALT) |
| `vat` | string | VAT amount included in the total |
| `card_last4` | string | Last four digits of the payment card |
| `item_count` | string | Number of items purchased, as printed |

### `bank_statement`

Monthly bank account statement for a private customer.

| field | type | description |
|---|---|---|
| `account_number` | string | Account number (format 1234.56.78903) |
| `period_start` | string | First day of the statement period (dd.mm.yyyy) |
| `period_end` | string | Last day of the statement period (dd.mm.yyyy) |
| `opening_balance` | string | Opening balance (inngående saldo) |
| `closing_balance` | string | Closing balance (utgående saldo) |
| `transaction_count` | string | Number of transactions listed |

### `holiday_survey`

Filled-in holiday preferences questionnaire (ferieundersøkelse).

| field | type | description |
|---|---|---|
| `name` | string | Name of the person filling in the form |
| `destination` | string | Preferred destination |
| `date_from` | string | Desired travel start date (dd.mm.yyyy) |
| `date_to` | string | Desired travel end date (dd.mm.yyyy) |
| `travellers` | string | Number of travellers |
| `budget_per_person` | string | Budget per person in NOK, as printed |
| `activities` | list | Activities whose checkbox is checked (☑) |
| `accommodation` | list | Accommodation types whose checkbox is checked (☑) |
| `pets` | string | Answer to 'Reiser du med kjæledyr?': 'Ja' or 'Nei' |
| `signed_date` | string | Date next to the signature (dd.mm.yyyy) |

### `invoice`

Invoice from a company to a private customer.

| field | type | description |
|---|---|---|
| `invoice_number` | string | Invoice number |
| `invoice_date` | string | Invoice date (dd.mm.yyyy) |
| `due_date` | string | Due date (dd.mm.yyyy) |
| `kid` | string | KID payment reference number |
| `account_number` | string | Account number to pay to (1234.56.78903) |
| `subtotal` | string | Sum excluding VAT |
| `vat` | string | VAT amount (25 %) |
| `total_due` | string | Total amount to pay including VAT |

### `payslip`

Monthly payslip from an employer to an employee.

| field | type | description |
|---|---|---|
| `employer` | string | Employer company name |
| `employee` | string | Employee full name |
| `period` | string | Pay period, as printed (dd.mm.yyyy - dd.mm.yyyy) |
| `gross_pay` | string | Gross pay for the period (brutto lønn) |
| `tax_withheld` | string | Tax withheld (forskuddstrekk) |
| `net_pay` | string | Net amount paid out |
| `payment_date` | string | Payment date (dd.mm.yyyy) |
| `account_number` | string | Employee bank account the pay is sent to |

### `rental_contract`

Residential tenancy agreement between a landlord and a tenant (2 pages).

| field | type | description |
|---|---|---|
| `landlord` | string | Name of the landlord (utleier) |
| `tenant` | string | Name of the tenant (leietaker) |
| `property_address` | string | Address of the rented home, as printed |
| `start_date` | string | Start date of the tenancy (dd.mm.yyyy) |
| `monthly_rent` | string | Monthly rent in NOK |
| `deposit` | string | Deposit amount in NOK |
| `notice_period` | string | Notice period, as printed (e.g. '3 måneder') |

### `newsletter`

Newsletter from a housing co-op board to residents.

| field | type | description |
|---|---|---|
| `issue_date` | string | Publication date of this issue (dd.mm.yyyy) |
| `meeting_date` | string | Date of the annual residents meeting (årsmøte) (dd.mm.yyyy) |
| `new_fee` | string | New monthly common cost for the example flat, in NOK |
| `dugnad_date` | string | Date of the spring/autumn dugnad (dd.mm.yyyy) |

