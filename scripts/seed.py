from pathlib import Path
import csv
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

# Project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env BEFORE importing the database module
load_dotenv(ROOT / ".env")

from sqlalchemy import select

from app.db import Base, engine, SessionLocal
from app.models import *
from app.security import hash_password


CATALOG_FILE = Path(__file__).resolve().parent / "catalog.csv"

# ---------------------------------------------------------------------------
# INITIAL ADMIN CONFIGURATION
# ---------------------------------------------------------------------------
#
# The Admin password is supplied through .env.
#
# Example:
#
# INITIAL_ADMIN_USERNAME=admin
# INITIAL_ADMIN_PASSWORD=ClientChosenStrongPassword
#
# Manager and Cashier passwords are NOT stored or created here.
# The Admin creates those accounts from the POS.
# ---------------------------------------------------------------------------

INITIAL_ADMIN_USERNAME = os.getenv(
    "INITIAL_ADMIN_USERNAME",
    "admin",
).strip()

INITIAL_ADMIN_PASSWORD = os.getenv(
    "INITIAL_ADMIN_PASSWORD",
)

if not INITIAL_ADMIN_PASSWORD:
    raise RuntimeError(
        "INITIAL_ADMIN_PASSWORD is required. "
        "Set it in the .env file before running the seed."
    )

if not INITIAL_ADMIN_USERNAME:
    raise RuntimeError(
        "INITIAL_ADMIN_USERNAME cannot be empty."
    )

# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------

Base.metadata.create_all(engine)

db = SessionLocal()

try:
    # -----------------------------------------------------------------------
    # 1. CREATE REQUIRED ROLES
    # -----------------------------------------------------------------------
    #
    # Roles are created automatically because the Admin needs to be able
    # to assign Manager/Cashier roles when creating staff accounts.
    #
    # No Manager or Cashier USERS are created here.
    # -----------------------------------------------------------------------

    role_limits = {
        "ADMIN": 100,
        "MANAGER": 20,
        "CASHIER": 5,
    }

    roles = {}

    for name, discount_limit in role_limits.items():
        role = db.scalar(
            select(Role).where(Role.name == name)
        )

        if not role:
            role = Role(
                name=name,
                discount_limit=discount_limit,
            )
            db.add(role)
            db.flush()

        roles[name] = role

    # -----------------------------------------------------------------------
    # 2. CREATE INITIAL ADMIN ACCOUNT
    # -----------------------------------------------------------------------
    #
    # The Admin is created only if the account does not already exist.
    #
    # IMPORTANT:
    # Running seed.py again will NOT reset the Admin password.
    # This prevents accidental password changes on an existing installation.
    # -----------------------------------------------------------------------

    admin = db.scalar(
        select(User).where(
            User.username == INITIAL_ADMIN_USERNAME
        )
    )

    if not admin:
        admin = User(
            username=INITIAL_ADMIN_USERNAME,
            full_name="System Administrator",
            role_id=roles["ADMIN"].id,
            password_hash=hash_password(
                INITIAL_ADMIN_PASSWORD
            ),
            is_active=True,
        )

        db.add(admin)
        db.commit()

        print(
            f"Initial Admin account created: "
            f"{INITIAL_ADMIN_USERNAME}"
        )

    else:
        print(
            f"Admin account already exists: "
            f"{INITIAL_ADMIN_USERNAME}"
        )

    # -----------------------------------------------------------------------
    # 3. LOAD PRODUCT CATALOGUE
    # -----------------------------------------------------------------------

    if not CATALOG_FILE.exists():
        raise FileNotFoundError(
            f"Catalogue file not found: {CATALOG_FILE}"
        )

    with CATALOG_FILE.open(
        newline="",
        encoding="utf-8",
    ) as fh:
        rows = list(csv.DictReader(fh))

    # -----------------------------------------------------------------------
    # 4. CREATE CATEGORIES
    # -----------------------------------------------------------------------

    category_names = []

    for row in rows:
        category_name = row["category"].strip()

        if (
            category_name
            and category_name not in category_names
        ):
            category_names.append(category_name)

    for name in category_names:
        category = db.scalar(
            select(Category).where(
                Category.name == name
            )
        )

        if not category:
            db.add(Category(name=name))

    # -----------------------------------------------------------------------
    # 5. CREATE BRANDS
    # -----------------------------------------------------------------------

    brand_names = []

    for row in rows:
        brand_name = row["brand"].strip()

        if (
            brand_name
            and brand_name not in brand_names
        ):
            brand_names.append(brand_name)

    for name in brand_names:
        brand = db.scalar(
            select(Brand).where(
                Brand.name == name
            )
        )

        if not brand:
            db.add(Brand(name=name))

    db.commit()

    # -----------------------------------------------------------------------
    # 6. IMPORT / UPDATE PRODUCTS AND VARIANTS
    # -----------------------------------------------------------------------

    imported = 0
    updated = 0
    skipped = []

    for row in rows:
        sku = row["sku"].strip()
        selling_price = row["selling_price"].strip()

        if not sku:
            skipped.append(
                (
                    row.get("variant_name", "Unknown product"),
                    "missing SKU",
                )
            )
            continue

        category_name = row["category"].strip()
        brand_name = row["brand"].strip()
        product_name = row["product_name"].strip()
        size = row["size"].strip()
        unit = row["unit"].strip() or "unit"

        # ---------------------------------------------------------------
        # CATEGORY
        # ---------------------------------------------------------------

        category = db.scalar(
            select(Category).where(
                Category.name == category_name
            )
        )

        if not category:
            skipped.append(
                (
                    row.get("variant_name", product_name),
                    "missing category",
                )
            )
            continue

        # ---------------------------------------------------------------
        # BRAND
        # ---------------------------------------------------------------

        brand = None

        if brand_name:
            brand = db.scalar(
                select(Brand).where(
                    Brand.name == brand_name
                )
            )

        # ---------------------------------------------------------------
        # PRODUCT
        # ---------------------------------------------------------------

        product = db.scalar(
            select(Product).where(
                Product.name == product_name
            )
        )

        if not product:
            product = Product(
                name=product_name,
                brand_id=brand.id if brand else None,
                category_id=category.id,
            )

            db.add(product)
            db.flush()

        else:
            product.category_id = category.id

            if brand:
                product.brand_id = brand.id

        # ---------------------------------------------------------------
        # PRODUCT VARIANT
        # ---------------------------------------------------------------

        variant = db.scalar(
            select(ProductVariant).where(
                ProductVariant.sku == sku
            )
        )

        if not variant:
            variant = ProductVariant(
                product_id=product.id,
                size=size,
                unit=unit,
                sku=sku,
                cost_price=Decimal("0"),
                selling_price=(
                    Decimal(selling_price)
                    if selling_price
                    else Decimal("0")
                ),
                wholesale_price=Decimal("0"),
                reorder_level=5,
            )

            db.add(variant)
            db.flush()

            # Opening stock is intentionally zero because the supplied
            # catalogue contains selling prices but no stock counts.
            db.add(
                Inventory(
                    variant_id=variant.id,
                    quantity=0,
                )
            )

            imported += 1

        else:
            variant.product_id = product.id
            variant.size = size
            variant.unit = unit

            if selling_price:
                variant.selling_price = Decimal(
                    selling_price
                )

            updated += 1

    # -----------------------------------------------------------------------
    # 7. COMMIT CATALOGUE
    # -----------------------------------------------------------------------

    db.commit()

    # -----------------------------------------------------------------------
    # 8. SUMMARY
    # -----------------------------------------------------------------------

    print()
    print("=" * 60)
    print("BLACK RHINO POS SEED COMPLETE")
    print("=" * 60)
    print()

    print(
        f"Admin account: {INITIAL_ADMIN_USERNAME}"
    )

    print(
        "Manager accounts: none created "
        "(Admin creates them)"
    )

    print(
        "Cashier accounts: none created "
        "(Admin creates them)"
    )

    print()

    print(
        f"Black Rhino catalogue imported: "
        f"{imported} new variants, "
        f"{updated} existing variants updated."
    )

    print(
        f"Source rows: {len(rows)}"
    )

    print(
        "Size normalization: "
        "1/2 and HALF -> 500ml; "
        "1/4 -> 250ml."
    )

    print(
        "Stock quantities remain 0 because "
        "the provided pages contain no stock counts."
    )

    print()

    if skipped:
        print("Skipped:")
        for item, reason in skipped:
            print(f"  - {item}: {reason}")

    print()
    print("=" * 60)

finally:
    db.close()