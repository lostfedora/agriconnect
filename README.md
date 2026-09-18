# Agrikonnect Recreated

Cleaned Django project with shared farmer registration for web portal and mobile API.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install django djangorestframework python-dotenv pillow
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

## Farmer account creation

Web portal:

```text
/accounts/farmer/request-otp/
/accounts/farmer/signup/
```

Mobile API:

```text
POST /api/farmers/register/
```

Example payload:

```json
{
  "full_name": "Test Farmer",
  "phone": "0771234567",
  "password": "123456",
  "district": "Kampala",
  "account_type": "farmer"
}
```

Android emulator base URL:

```text
http://10.0.2.2:8000
```

Real phone base URL: use your computer LAN IP, for example:

```text
http://192.168.1.137:8000
```

## Batch-based farm records update

This rebuild adds production batches below farm projects so records stay clean across seasons and production cycles.

New structure:

```text
Farm
  -> Project, e.g. Maize, Broilers, Fish Pond
      -> Production Batch, e.g. 2026A, Flock 001, Pond Cycle 01
          -> Activities
          -> Expenses
          -> Harvests
          -> Sales
```

Key additions:

- `ProductionBatch` model with batch code, season, status, start/end dates, area/units, expected output, expected cost, and expected revenue.
- Harvest, expense, sales, and activity records now support a `batch` foreign key.
- Batch dashboard pages show batch revenue, expenses, profit, harvested quantity, sold quantity, and stock balance.
- Web routes added under `/farms/batches/`.
- API route added under `/api/batches/` with a batch profit summary action.

After updating your environment, run:

```bash
python manage.py migrate
```

Note: Django was not installed in the rebuild container, so migrations were written manually and Python syntax was checked with `py_compile`.

## Farmer API product images

Farmers can now upload product descriptions and images through the API.

Create a listing with images using `multipart/form-data`:

`POST /api/farmers/listings/`

Fields include the existing listing fields plus:

- `description` - optional product description
- `uploaded_images` - one or more image files

Add images to an existing listing:

`POST /api/farmers/listings/<listing_id>/images/`

Use one of these multipart file field names:

- `images`
- `uploaded_images`
- `image`

Listing API responses now include:

- `description`
- `images[]`
- `primary_image_url`

## Farmer app read API

The farmer mobile app can now fetch the farmer account, products, expenses, projects, batches, and sales through clearer read endpoints under `/api/farmers/`.

Use token auth on every request after login:

```http
Authorization: Token <token>
```

Important endpoints:

- `GET /api/farmers/details/` - farmer profile plus high-level counts and totals.
- `GET /api/farmers/app-data/?limit=30` - one payload for the app home/cache screen: farmer, farms, projects, batches, farmer products, expenses, and sales.
- `GET /api/farmers/app-data/?include_open_products=true` - also includes open marketplace products.
- `GET /api/farmers/listings/` or `GET /api/farmers/my-listings/` - products uploaded by the logged-in farmer.
- `GET /api/farmers/products/` - open marketplace products.
- `GET /api/farmers/product/<product_id>/` - product detail for the farmer's own product or any open marketplace product.
- `GET /api/farmers/expenses/` - farmer expenses.
- `GET /api/farmers/expenses/<expense_id>/` - expense detail.
- `GET /api/farmers/me/` - farmer account details only.

Product responses include `farm_name`, `farmer_name`, `description`, `images[]`, and `primary_image_url`. Expense, sale, project, and batch responses include readable farm/project/batch names where available so the Flutter app can display clean cards without extra lookups.

## Farmer profile API

Authenticated farmer profile data is available at:

- `GET /api/farmers/me/` - retrieve the profile
- `PATCH /api/farmers/me/` - update selected profile fields
- `PUT /api/farmers/me/` - accepted as a safe partial update

Send the DRF token as `Authorization: Token <token>`.

Writable fields include `full_name`, `email`, `district`, `national_id`, `gender`, `date_of_birth`, `village`, `subcounty`, `primary_crop`, and `farming_experience_years`.

`phone`, `account_type`, and verification flags are read-only. Phone changes should use an OTP-verified flow.

Example PATCH body:

```json
{
  "full_name": "Timothy Atwanzire",
  "district": "Kasese",
  "village": "Kisinga",
  "subcounty": "Kisinga",
  "primary_crop": "Coffee",
  "farming_experience_years": 5
}
```

Copy `.env.example` to `.env` and provide the production database, Django secret key, host, CSRF, and Yoola SMS settings before deployment.
