import os
import json
import secrets
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing the database module.
# This allows both `python ...` and Uvicorn to find DATABASE_URL/APP_SECRET.
load_dotenv()

from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .db import Base, engine, get_db
from .models import *
from .security import (
    hash_password,
    verify_password,
    get_current_user,
    require_roles,
)


# ---------------------------------------------------------------------------
# APPLICATION SETUP
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Black Rhino Liquor Store POS",
    version="1.1.0",
)

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError(
        "APP_SECRET is required. Generate a long random secret for production."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    max_age=60 * 60 * 12,
    same_site="strict",
    # Development on localhost normally uses HTTP.
    # Set SESSION_HTTPS_ONLY=1 in production behind HTTPS.
    https_only=os.getenv(
        "SESSION_HTTPS_ONLY", "0"
    ).lower() in {"1", "true", "yes"},
)


# ---------------------------------------------------------------------------
# SECURITY HEADERS
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff",
    )
    response.headers.setdefault(
        "X-Frame-Options",
        "DENY",
    )
    response.headers.setdefault(
        "Referrer-Policy",
        "same-origin",
    )
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )

    if request.url.path.startswith("/api/") or request.url.path in {
        "/login",
        "/logout",
    }:
        response.headers.setdefault(
            "Cache-Control",
            "no-store",
        )

    return response


# ---------------------------------------------------------------------------
# STATIC FILES / TEMPLATES
# ---------------------------------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def money(value):
    return f"{float(value or 0):,.2f}"


def audit(
    db,
    user_id,
    action,
    entity_type=None,
    entity_id=None,
    detail=None,
):
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )


# ---------------------------------------------------------------------------
# STARTUP
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    """
    Creates missing tables for first-run environments.

    PostgreSQL remains the authoritative production database.
    Production deployments should use the supplied migration/seed process.
    """
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# SERVICE WORKER
# ---------------------------------------------------------------------------

@app.get("/service-worker.js")
def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "service-worker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# Compatibility alias.
# Some browsers/frontend code may request /sw.js.
@app.get("/sw.js")
def service_worker_alias():
    return FileResponse(
        BASE_DIR / "static" / "service-worker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# OFFLINE POS
# ---------------------------------------------------------------------------

@app.get("/offline-pos", response_class=HTMLResponse)
def offline_pos(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="offline_pos.html",
    )


# ---------------------------------------------------------------------------
# HOME
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    return RedirectResponse(
        "/dashboard",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
    )


@app.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()

    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")

    if not username or not password:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Please enter your username and password."},
            status_code=400,
        )

    user = db.scalar(
        select(User)
        .options(joinedload(User.role))
        .where(User.username == username)
    )

    if (
        not user
        or not user.is_active
        or not verify_password(password, user.password_hash)
    ):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password."},
            status_code=401,
        )

    request.session.clear()
    request.session["user"] = {
        "id": user.id,
        "username": user.username,
        "name": user.full_name,
        "role": user.role.name,
    }

    audit(db, user.id, "LOGIN", "User", user.id)
    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

    user = db.scalar(
        select(User)
        .options(joinedload(User.role))
        .where(User.username == username)
    )

    if (
        not user
        or not user.is_active
        or not verify_password(password, user.password_hash)
    ):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Invalid username or password."
            },
            status_code=401,
        )

    # Replace the existing session contents after successful login.
    # This prevents stale authentication data from surviving login.
    request.session.clear()

    request.session["user"] = {
        "id": user.id,
        "username": user.username,
        "name": user.full_name,
        "role": user.role.name,
    }

    audit(
        db,
        user.id,
        "LOGIN",
        "User",
        user.id,
    )

    db.commit()

    return RedirectResponse(
        "/dashboard",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------------------------

@app.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    user = request.session.get("user")

    if user:
        audit(
            db,
            user["id"],
            "LOGOUT",
            "User",
            user["id"],
        )
        db.commit()

    request.session.clear()

    return RedirectResponse(
        "/login",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = datetime.utcnow().date()
    start = datetime.combine(
        today,
        datetime.min.time(),
    )

    sales_today = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Sale.total),
                    0,
                )
            ).where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
        )
        or 0
    )

    tx_today = (
        db.scalar(
            select(func.count(Sale.id)).where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
        )
        or 0
    )

    low = (
        db.execute(
            select(ProductVariant)
            .join(Inventory)
            .join(Product)
            .where(
                ProductVariant.is_active == True,
                Inventory.quantity <= ProductVariant.reorder_level,
            )
            .limit(10)
        )
        .scalars()
        .all()
    )

    recent = (
        db.execute(
            select(Sale)
            .options(joinedload(Sale.cashier))
            .order_by(desc(Sale.id))
            .limit(8)
        )
        .scalars()
        .unique()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "sales_today": money(sales_today),
            "tx_today": tx_today,
            "low_stock": low,
            "recent": recent,
            "money": money,
        },
    )


# ---------------------------------------------------------------------------
# POS
# ---------------------------------------------------------------------------

@app.get("/pos", response_class=HTMLResponse)
def pos_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    variants = (
        db.execute(
            select(ProductVariant)
            .options(
                joinedload(ProductVariant.product),
                joinedload(ProductVariant.inventory),
            )
            .where(ProductVariant.is_active == True)
            .order_by(ProductVariant.id)
        )
        .scalars()
        .unique()
        .all()
    )

    shift = db.scalar(
        select(Shift).where(
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="pos.html",
        context={
            "user": user,
            "variants": variants,
            "shift": shift,
            "money": money,
        },
    )


# ---------------------------------------------------------------------------
# SALE VALIDATION / CREATION
# ---------------------------------------------------------------------------

def _sale_payload_validate(
    db: Session,
    user: dict,
    payload: dict,
):
    if user["role"] not in (
        "ADMIN",
        "MANAGER",
        "CASHIER",
    ):
        raise HTTPException(403)

    items = payload.get("items", [])

    if not items:
        raise HTTPException(
            400,
            "Cart is empty.",
        )

    if not payload.get("age_verified"):
        raise HTTPException(
            400,
            "Age verification is required.",
        )

    client_id = str(
        payload.get("client_id") or ""
    ).strip()[:64]

    if not client_id:
        client_id = secrets.token_hex(16)

    # Idempotency check for normal and offline sales.
    existing = db.scalar(
        select(Sale).where(
            Sale.client_id == client_id
        )
    )

    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "sale_id": existing.id,
            "receipt": existing.receipt_number,
            "total": float(existing.total),
            "client_id": client_id,
        }

    shift_id = (
        int(payload.get("shift_id"))
        if payload.get("shift_id")
        else -1
    )

    shift = db.scalar(
        select(Shift).where(
            Shift.id == shift_id,
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    if not shift:
        raise HTTPException(
            409,
            "The cashier shift is not open. "
            "Reconnect and open a shift before syncing or selling.",
        )

    requested_discount = Decimal(
        str(
            payload.get("discount", 0)
            or 0
        )
    )

    if requested_discount < 0:
        raise HTTPException(
            400,
            "Invalid discount.",
        )

    subtotal = Decimal("0")
    validated = []

    try:
        variant_ids = sorted(
            {
                int(row["variant_id"])
                for row in items
            }
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid sale item.",
        )

    for variant_id in variant_ids:
        variant = db.scalar(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.is_active == True,
            )
        )

        if not variant:
            raise HTTPException(
                400,
                "Product is unavailable.",
            )

        # PostgreSQL row lock serializes competing tills.
        inv = db.scalar(
            select(Inventory)
            .where(
                Inventory.variant_id == variant.id
            )
            .with_for_update()
        )

        if not inv:
            raise HTTPException(
                400,
                "Product inventory is unavailable.",
            )

        rows_for_variant = [
            row
            for row in items
            if int(row["variant_id"]) == variant_id
        ]

        try:
            qty = sum(
                int(row["quantity"])
                for row in rows_for_variant
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            raise HTTPException(
                400,
                "Invalid product quantity.",
            )

        if qty <= 0:
            raise HTTPException(
                400,
                "Quantity must be greater than zero.",
            )

        if inv.quantity < qty:
            raise HTTPException(
                409,
                f"Insufficient stock for "
                f"{variant.product.name}. "
                f"Current stock: {inv.quantity}.",
            )

        unit = Decimal(
            str(variant.selling_price)
        )

        line = unit * qty
        subtotal += line

        validated.append(
            (
                variant,
                inv,
                qty,
                unit,
                line,
            )
        )

    # -----------------------------------------------------------------------
    # DISCOUNT VALIDATION
    # -----------------------------------------------------------------------

    role = db.scalar(
        select(Role).where(
            Role.name == user["role"]
        )
    )

    max_discount = Decimal(
        str(
            role.discount_limit
            if role
            else 0
        )
    )

    if requested_discount > subtotal:
        raise HTTPException(
            400,
            "Invalid discount.",
        )

    if (
        subtotal
        and (
            requested_discount
            / subtotal
            * 100
        ) > max_discount
    ):
        raise HTTPException(
            403,
            f"Your role can discount up to "
            f"{max_discount}%.",
        )

    total = subtotal - requested_discount

    # -----------------------------------------------------------------------
    # PAYMENT VALIDATION
    # -----------------------------------------------------------------------

    payments = payload.get(
        "payments",
        [],
    )

    allowed_methods = {
        "CASH",
        "MPESA",
        "CARD",
    }

    if not payments:
        raise HTTPException(
            400,
            "A payment is required.",
        )

    paid = Decimal("0")
    clean_payments = []

    for pay in payments:
        method = str(
            pay.get("method") or ""
        ).upper()

        try:
            amount = Decimal(
                str(
                    pay.get("amount", 0)
                    or 0
                )
            )
        except Exception:
            raise HTTPException(
                400,
                "Invalid payment amount.",
            )

        if (
            method not in allowed_methods
            or amount <= 0
        ):
            raise HTTPException(
                400,
                "Invalid payment.",
            )

        paid += amount

        clean_payments.append(
            (
                method,
                amount,
                str(
                    pay.get("reference") or ""
                )[:100],
                str(
                    pay.get("phone") or ""
                )[:40],
            )
        )

    if paid != total:
        raise HTTPException(
            400,
            f"Payment total must equal "
            f"KES {money(total)}.",
        )

    # -----------------------------------------------------------------------
    # CREATE SALE
    # -----------------------------------------------------------------------

    receipt = (
        f"BR-"
        f"{datetime.utcnow().strftime('%Y%m%d')}-"
        f"{secrets.token_hex(4).upper()}"
    )

    sale = Sale(
        receipt_number=receipt,
        client_id=client_id,
        customer_id=payload.get("customer_id"),
        cashier_id=user["id"],
        shift_id=shift.id,
        subtotal=subtotal,
        discount=requested_discount,
        tax=0,
        total=total,
        age_verified=True,
    )

    db.add(sale)
    db.flush()

    discount_ratio = (
        requested_discount / subtotal
        if subtotal
        else Decimal("0")
    )

    # -----------------------------------------------------------------------
    # INVENTORY + SALE ITEMS
    # -----------------------------------------------------------------------

    for (
        variant,
        inv,
        qty,
        unit,
        line,
    ) in validated:

        line_discount = (
            line * discount_ratio
        ).quantize(
            Decimal("0.01")
        )

        item_total = (
            line - line_discount
        )

        db.add(
            SaleItem(
                sale_id=sale.id,
                variant_id=variant.id,
                quantity=qty,
                unit_price=unit,
                cost_price=variant.cost_price,
                discount=line_discount,
                total=item_total,
            )
        )

        previous_quantity = inv.quantity

        inv.quantity -= qty

        db.add(
            StockMovement(
                variant_id=variant.id,
                movement_type="SALE",
                quantity=-qty,
                previous_quantity=previous_quantity,
                new_quantity=inv.quantity,
                reference_type="SALE",
                reference_id=sale.id,
                reason="POS sale",
                created_by=user["id"],
            )
        )

    # -----------------------------------------------------------------------
    # PAYMENTS
    # -----------------------------------------------------------------------

    for (
        method,
        amount,
        reference,
        phone,
    ) in clean_payments:

        db.add(
            Payment(
                sale_id=sale.id,
                payment_method=method,
                amount=amount,
                status="COMPLETED",
                transaction_reference=(
                    reference or None
                ),
                phone_number=(
                    phone or None
                ),
            )
        )

    # -----------------------------------------------------------------------
    # AUDIT
    # -----------------------------------------------------------------------

    audit(
        db,
        user["id"],
        "SALE_CREATED",
        "Sale",
        sale.id,
        json.dumps(
            {
                "receipt": receipt,
                "total": str(total),
                "client_id": client_id,
            }
        ),
    )

    return {
        "ok": True,
        "duplicate": False,
        "sale_id": sale.id,
        "receipt": receipt,
        "total": float(total),
        "client_id": client_id,
    }


# ---------------------------------------------------------------------------
# NORMAL POS SALE
# ---------------------------------------------------------------------------

@app.post("/api/sales")
def create_sale(
    payload: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = _sale_payload_validate(
            db,
            user,
            payload,
        )

        if not result.get("duplicate"):
            db.commit()

        return result

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError:
        db.rollback()

        client_id = str(
            payload.get("client_id") or ""
        )[:64]

        existing = db.scalar(
            select(Sale).where(
                Sale.client_id == client_id
            )
        )

        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "sale_id": existing.id,
                "receipt": existing.receipt_number,
                "total": float(existing.total),
                "client_id": client_id,
            }

        raise HTTPException(
            409,
            "Sale could not be committed. Please retry.",
        )


# ---------------------------------------------------------------------------
# OFFLINE BOOTSTRAP
# ---------------------------------------------------------------------------

@app.get("/api/offline/bootstrap")
def offline_bootstrap(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shift = db.scalar(
        select(Shift).where(
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    variants = (
        db.execute(
            select(ProductVariant)
            .options(
                joinedload(ProductVariant.product),
                joinedload(ProductVariant.inventory),
            )
            .where(
                ProductVariant.is_active == True
            )
            .order_by(ProductVariant.id)
        )
        .scalars()
        .unique()
        .all()
    )

    return {
        "server_time": datetime.utcnow().isoformat(),
        "user": user,
        "shift": (
            {
                "id": shift.id,
                "opened_at": shift.opened_at.isoformat(),
            }
            if shift
            else None
        ),
        "products": [
            {
                "id": v.id,
                "name": v.product.name,
                "size": v.size,
                "sku": v.sku,
                "barcode": v.barcode,
                "price": float(v.selling_price),
                "stock": (
                    v.inventory.quantity
                    if v.inventory
                    else 0
                ),
            }
            for v in variants
        ],
    }


# ---------------------------------------------------------------------------
# OFFLINE SALE SYNCHRONIZATION
# ---------------------------------------------------------------------------

@app.post("/api/sync/sales")
def sync_sales(
    payload: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    queue = payload.get(
        "sales",
        [],
    )

    if (
        not isinstance(queue, list)
        or len(queue) > 100
    ):
        raise HTTPException(
            400,
            "Invalid sync batch.",
        )

    results = []

    for queued in queue:
        client_id = str(
            queued.get("client_id") or ""
        )[:64]

        if not client_id:
            results.append(
                {
                    "client_id": "",
                    "ok": False,
                    "error": "Missing client_id.",
                }
            )
            continue

        try:
            result = _sale_payload_validate(
                db,
                user,
                queued,
            )

            if not result.get("duplicate"):
                db.commit()

            results.append(result)

        except HTTPException as exc:
            db.rollback()

            results.append(
                {
                    "client_id": client_id,
                    "ok": False,
                    "error": exc.detail,
                    "status": exc.status_code,
                }
            )

        except IntegrityError:
            db.rollback()

            existing = db.scalar(
                select(Sale).where(
                    Sale.client_id == client_id
                )
            )

            if existing:
                results.append(
                    {
                        "ok": True,
                        "duplicate": True,
                        "sale_id": existing.id,
                        "receipt": existing.receipt_number,
                        "total": float(existing.total),
                        "client_id": client_id,
                    }
                )
            else:
                results.append(
                    {
                        "client_id": client_id,
                        "ok": False,
                        "error": (
                            "Database conflict; "
                            "retry required."
                        ),
                        "status": 409,
                    }
                )

    return {
        "ok": True,
        "results": results,
    }


# ---------------------------------------------------------------------------
# RECEIPT
# ---------------------------------------------------------------------------

@app.get(
    "/receipt/{sale_id}",
    response_class=HTMLResponse,
)
def receipt(
    sale_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sale = (
        db.execute(
            select(Sale)
            .options(
                joinedload(Sale.items)
                .joinedload(SaleItem.variant)
                .joinedload(ProductVariant.product),
                joinedload(Sale.payments),
                joinedload(Sale.cashier),
            )
            .where(Sale.id == sale_id)
        )
        .unique()
        .scalar_one_or_none()
    )

    if not sale:
        raise HTTPException(404)

    if (
        user["role"] == "CASHIER"
        and sale.cashier_id != user["id"]
    ):
        raise HTTPException(403)

    return templates.TemplateResponse(
        request=request,
        name="receipt.html",
        context={
            "sale": sale,
            "money": money,
        },
    )


# ---------------------------------------------------------------------------
# INVENTORY
# ---------------------------------------------------------------------------

@app.get(
    "/inventory",
    response_class=HTMLResponse,
)
def inventory(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    variants = (
        db.execute(
            select(ProductVariant)
            .options(
                joinedload(ProductVariant.product),
                joinedload(ProductVariant.inventory),
            )
            .order_by(ProductVariant.id)
        )
        .scalars()
        .unique()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="inventory.html",
        context={
            "user": user,
            "variants": variants,
            "money": money,
        },
    )


@app.post("/api/inventory/adjust")
def adjust_stock(
    payload: dict,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    try:
        variant_id = int(
            payload["variant_id"]
        )
        new_qty = int(
            payload["new_quantity"]
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid inventory adjustment.",
        )

    variant = db.scalar(
        select(ProductVariant)
        .options(
            joinedload(
                ProductVariant.inventory
            )
        )
        .where(
            ProductVariant.id == variant_id
        )
    )

    if (
        not variant
        or not variant.inventory
    ):
        raise HTTPException(404)

    if new_qty < 0:
        raise HTTPException(
            400,
            "Stock cannot be negative.",
        )

    inv = variant.inventory
    previous_quantity = inv.quantity

    inv.quantity = new_qty

    reason = str(
        payload.get(
            "reason",
            "Manual adjustment",
        )
    )[:255]

    db.add(
        StockMovement(
            variant_id=variant.id,
            movement_type="ADJUSTMENT",
            quantity=new_qty - previous_quantity,
            previous_quantity=previous_quantity,
            new_quantity=new_qty,
            reason=reason,
            created_by=user["id"],
        )
    )

    audit(
        db,
        user["id"],
        "STOCK_ADJUSTED",
        "ProductVariant",
        variant.id,
        reason,
    )

    db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# PRODUCTS
# ---------------------------------------------------------------------------

@app.get(
    "/products",
    response_class=HTMLResponse,
)
def products(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    variants = (
        db.execute(
            select(ProductVariant)
            .options(
                joinedload(ProductVariant.product),
                joinedload(ProductVariant.inventory),
                joinedload(
                    ProductVariant.product
                ).joinedload(Product.category),
                joinedload(
                    ProductVariant.product
                ).joinedload(Product.brand),
            )
            .order_by(ProductVariant.id)
        )
        .scalars()
        .unique()
        .all()
    )

    categories = db.scalars(
        select(Category).order_by(
            Category.name
        )
    ).all()

    brands = db.scalars(
        select(Brand).order_by(
            Brand.name
        )
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "user": user,
            "variants": variants,
            "categories": categories,
            "brands": brands,
            "money": money,
        },
    )


@app.post("/api/products")
def add_product(
    payload: dict,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    product_name = str(
        payload.get("name") or ""
    ).strip()

    sku = str(
        payload.get("sku") or ""
    ).strip()

    if not product_name:
        raise HTTPException(
            400,
            "Product name is required.",
        )

    if not sku:
        raise HTTPException(
            400,
            "SKU is required.",
        )

    try:
        cost_price = Decimal(
            str(
                payload.get(
                    "cost_price",
                    0,
                )
            )
        )

        selling_price = Decimal(
            str(
                payload.get(
                    "selling_price",
                    0,
                )
            )
        )

        wholesale_price = Decimal(
            str(
                payload.get(
                    "wholesale_price",
                    0,
                )
            )
        )

        opening_stock = int(
            payload.get(
                "opening_stock",
                0,
            )
        )

        reorder_level = int(
            payload.get(
                "reorder_level",
                5,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid product values.",
        )

    if (
        cost_price < 0
        or selling_price < 0
        or wholesale_price < 0
        or opening_stock < 0
        or reorder_level < 0
    ):
        raise HTTPException(
            400,
            "Product values cannot be negative.",
        )

    product = Product(
        name=product_name,
        category_id=payload.get(
            "category_id"
        ),
        brand_id=payload.get(
            "brand_id"
        ),
        description=payload.get(
            "description"
        ),
    )

    variant = ProductVariant(
        size=payload.get(
            "size",
            "",
        ),
        unit=payload.get(
            "unit",
            "unit",
        ),
        sku=sku,
        barcode=(
            payload.get("barcode")
            or None
        ),
        cost_price=cost_price,
        selling_price=selling_price,
        wholesale_price=wholesale_price,
        reorder_level=reorder_level,
    )

    product.variants.append(
        variant
    )

    db.add(product)
    db.flush()

    db.add(
        Inventory(
            variant_id=variant.id,
            quantity=opening_stock,
        )
    )

    audit(
        db,
        user["id"],
        "PRODUCT_CREATED",
        "Product",
        product.id,
        product.name,
    )

    db.commit()

    return {
        "ok": True,
        "product_id": product.id,
        "variant_id": variant.id,
    }


# ---------------------------------------------------------------------------
# CUSTOMERS
# ---------------------------------------------------------------------------

@app.get(
    "/customers",
    response_class=HTMLResponse,
)
def customers(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Customer).order_by(
            Customer.name
        )
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="customers.html",
        context={
            "user": user,
            "customers": rows,
            "money": money,
        },
    )


@app.post("/api/customers")
def add_customer(
    payload: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = str(
        payload.get("name") or ""
    ).strip()

    if not name:
        raise HTTPException(
            400,
            "Customer name is required.",
        )

    try:
        credit_limit = Decimal(
            str(
                payload.get(
                    "credit_limit",
                    0,
                )
            )
        )
    except Exception:
        raise HTTPException(
            400,
            "Invalid credit limit.",
        )

    if credit_limit < 0:
        raise HTTPException(
            400,
            "Credit limit cannot be negative.",
        )

    customer = Customer(
        name=name,
        phone=payload.get("phone"),
        email=payload.get("email"),
        customer_type=payload.get(
            "customer_type",
            "RETAIL",
        ),
        credit_limit=credit_limit,
    )

    db.add(customer)
    db.flush()

    audit(
        db,
        user["id"],
        "CUSTOMER_CREATED",
        "Customer",
        customer.id,
        customer.name,
    )

    db.commit()

    return {
        "ok": True,
        "customer_id": customer.id,
    }


# ---------------------------------------------------------------------------
# SUPPLIERS
# ---------------------------------------------------------------------------

@app.get(
    "/suppliers",
    response_class=HTMLResponse,
)
def suppliers(
    request: Request,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Supplier).order_by(
            Supplier.name
        )
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="suppliers.html",
        context={
            "user": user,
            "suppliers": rows,
        },
    )


@app.post("/api/suppliers")
def add_supplier(
    payload: dict,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    name = str(
        payload.get("name") or ""
    ).strip()

    if not name:
        raise HTTPException(
            400,
            "Supplier name is required.",
        )

    supplier = Supplier(
        name=name,
        phone=payload.get("phone"),
        email=payload.get("email"),
        address=payload.get("address"),
    )

    db.add(supplier)
    db.flush()

    audit(
        db,
        user["id"],
        "SUPPLIER_CREATED",
        "Supplier",
        supplier.id,
        supplier.name,
    )

    db.commit()

    return {
        "ok": True,
        "supplier_id": supplier.id,
    }


# ---------------------------------------------------------------------------
# PURCHASES
# ---------------------------------------------------------------------------

@app.get(
    "/purchases",
    response_class=HTMLResponse,
)
def purchases(
    request: Request,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Purchase)
            .options(
                joinedload(Purchase.supplier),
                joinedload(
                    Purchase.items
                )
                .joinedload(
                    PurchaseItem.variant
                )
                .joinedload(
                    ProductVariant.product
                ),
            )
            .order_by(desc(Purchase.id))
            .limit(100)
        )
        .scalars()
        .unique()
        .all()
    )

    suppliers = db.scalars(
        select(Supplier).order_by(
            Supplier.name
        )
    ).all()

    variants = (
        db.execute(
            select(ProductVariant)
            .options(
                joinedload(
                    ProductVariant.product
                )
            )
            .where(
                ProductVariant.is_active
                == True
            )
        )
        .scalars()
        .unique()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="purchases.html",
        context={
            "user": user,
            "purchases": rows,
            "suppliers": suppliers,
            "variants": variants,
            "money": money,
        },
    )


@app.post("/api/purchases")
def add_purchase(
    payload: dict,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    items = payload.get(
        "items",
        [],
    )

    if not items:
        raise HTTPException(
            400,
            "No purchase items.",
        )

    try:
        supplier_id = int(
            payload["supplier_id"]
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid supplier.",
        )

    reference_number = str(
        payload.get(
            "reference_number",
            "",
        )
    ).strip()

    if not reference_number:
        raise HTTPException(
            400,
            "Purchase reference number is required.",
        )

    total = Decimal("0")

    purchase = Purchase(
        supplier_id=supplier_id,
        reference_number=reference_number,
        created_by=user["id"],
        status="RECEIVED",
    )

    db.add(purchase)
    db.flush()

    for row in items:
        try:
            variant_id = int(
                row["variant_id"]
            )
            quantity = int(
                row["quantity"]
            )
            cost = Decimal(
                str(row["unit_cost"])
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            raise HTTPException(
                400,
                "Invalid purchase item.",
            )

        if quantity <= 0:
            raise HTTPException(
                400,
                "Purchase quantity must be greater than zero.",
            )

        if cost < 0:
            raise HTTPException(
                400,
                "Purchase cost cannot be negative.",
            )

        variant = db.scalar(
            select(ProductVariant)
            .options(
                joinedload(
                    ProductVariant.inventory
                )
            )
            .where(
                ProductVariant.id
                == variant_id
            )
        )

        if (
            not variant
            or not variant.inventory
        ):
            raise HTTPException(
                404,
                "Product inventory not found.",
            )

        line = (
            Decimal(quantity)
            * cost
        )

        total += line

        purchase.items.append(
            PurchaseItem(
                variant_id=variant.id,
                quantity=quantity,
                unit_cost=cost,
                total=line,
            )
        )

        inv = variant.inventory
        previous_quantity = inv.quantity
        inv.quantity += quantity

        db.add(
            StockMovement(
                variant_id=variant.id,
                movement_type="PURCHASE",
                quantity=quantity,
                previous_quantity=previous_quantity,
                new_quantity=inv.quantity,
                reference_type="PURCHASE",
                reference_id=purchase.id,
                reason="Goods received",
                created_by=user["id"],
            )
        )

    purchase.total = total

    audit(
        db,
        user["id"],
        "PURCHASE_RECEIVED",
        "Purchase",
        purchase.id,
        purchase.reference_number,
    )

    db.commit()

    return {
        "ok": True,
        "purchase_id": purchase.id,
    }


# ---------------------------------------------------------------------------
# EXPENSES
# ---------------------------------------------------------------------------

@app.get(
    "/expenses",
    response_class=HTMLResponse,
)
def expenses(
    request: Request,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Expense)
            .order_by(desc(Expense.id))
            .limit(100)
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="expenses.html",
        context={
            "user": user,
            "expenses": rows,
            "money": money,
        },
    )


@app.post("/api/expenses")
def add_expense(
    payload: dict,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    try:
        amount = Decimal(
            str(
                payload["amount"]
            )
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid expense amount.",
        )

    if amount <= 0:
        raise HTTPException(
            400,
            "Expense amount must be greater than zero.",
        )

    category = str(
        payload.get(
            "category",
            "",
        )
    ).strip()

    if not category:
        raise HTTPException(
            400,
            "Expense category is required.",
        )

    expense = Expense(
        category=category,
        amount=amount,
        note=payload.get("note"),
        created_by=user["id"],
    )

    db.add(expense)

    audit(
        db,
        user["id"],
        "EXPENSE_CREATED",
        "Expense",
        None,
        payload.get("note"),
    )

    db.commit()

    return {
        "ok": True,
        "expense_id": expense.id,
    }


# ---------------------------------------------------------------------------
# SHIFTS
# ---------------------------------------------------------------------------

@app.get(
    "/shifts",
    response_class=HTMLResponse,
)
def shifts(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Shift)
            .options(
                joinedload(Shift.user)
            )
            .order_by(desc(Shift.id))
            .limit(100)
        )
        .scalars()
        .unique()
        .all()
    )

    current = db.scalar(
        select(Shift).where(
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="shifts.html",
        context={
            "user": user,
            "shifts": rows,
            "current": current,
            "money": money,
        },
    )


@app.post("/api/shifts/open")
def open_shift(
    payload: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.scalar(
        select(Shift).where(
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    if existing:
        raise HTTPException(
            400,
            "You already have an open shift.",
        )

    try:
        opening_cash = Decimal(
            str(
                payload.get(
                    "opening_cash",
                    0,
                )
            )
        )
    except Exception:
        raise HTTPException(
            400,
            "Invalid opening cash.",
        )

    if opening_cash < 0:
        raise HTTPException(
            400,
            "Opening cash cannot be negative.",
        )

    shift = Shift(
        user_id=user["id"],
        opening_cash=opening_cash,
    )

    db.add(shift)
    db.flush()

    audit(
        db,
        user["id"],
        "SHIFT_OPENED",
        "Shift",
        shift.id,
    )

    db.commit()

    return {
        "ok": True,
        "shift_id": shift.id,
    }


@app.post("/api/shifts/close")
def close_shift(
    payload: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shift = db.scalar(
        select(Shift).where(
            Shift.user_id == user["id"],
            Shift.status == "OPEN",
        )
    )

    if not shift:
        raise HTTPException(
            400,
            "No open shift.",
        )

    sales_cash = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Payment.amount),
                    0,
                )
            )
            .join(Sale)
            .where(
                Sale.cashier_id == user["id"],
                Sale.created_at >= shift.opened_at,
                Payment.payment_method == "CASH",
                Payment.status == "COMPLETED",
            )
        )
        or 0
    )

    expenses = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Expense.amount),
                    0,
                )
            ).where(
                Expense.created_by == user["id"],
                Expense.created_at >= shift.opened_at,
            )
        )
        or 0
    )

    expected = (
        Decimal(str(shift.opening_cash))
        + Decimal(str(sales_cash))
        - Decimal(str(expenses))
    )

    try:
        actual = Decimal(
            str(
                payload["closing_cash"]
            )
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            400,
            "Invalid closing cash.",
        )

    if actual < 0:
        raise HTTPException(
            400,
            "Closing cash cannot be negative.",
        )

    shift.expected_cash = expected
    shift.closing_cash = actual
    shift.cash_difference = (
        actual - expected
    )
    shift.status = "CLOSED"
    shift.closed_at = datetime.utcnow()

    audit(
        db,
        user["id"],
        "SHIFT_CLOSED",
        "Shift",
        shift.id,
        f"Expected {expected}; Actual {actual}",
    )

    db.commit()

    return {
        "ok": True,
        "expected": float(expected),
        "difference": float(
            actual - expected
        ),
    }


# ---------------------------------------------------------------------------
# REPORTS
# ---------------------------------------------------------------------------

@app.get(
    "/reports",
    response_class=HTMLResponse,
)
def reports(
    request: Request,
    user=Depends(
        require_roles(
            "ADMIN",
            "MANAGER",
        )
    ),
    db: Session = Depends(get_db),
):
    today = datetime.utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
    )

    sales = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Sale.total),
                    0,
                )
            ).where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
        )
        or 0
    )

    expenses_total = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Expense.amount),
                    0,
                )
            ).where(
                Expense.created_at >= start
            )
        )
        or 0
    )

    top = (
        db.execute(
            select(
                Product.name,
                func.sum(
                    SaleItem.quantity
                ).label("qty"),
                func.sum(
                    SaleItem.total
                ).label("value"),
            )
            .join(
                ProductVariant,
                ProductVariant.product_id
                == Product.id,
            )
            .join(
                SaleItem,
                SaleItem.variant_id
                == ProductVariant.id,
            )
            .join(
                Sale,
                Sale.id == SaleItem.sale_id,
            )
            .where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
            .group_by(Product.id)
            .order_by(desc("qty"))
            .limit(10)
        )
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "user": user,
            "sales": money(sales),
            "expenses": money(expenses_total),
            "top": top,
            "money": money,
        },
    )


# ---------------------------------------------------------------------------
# USERS
# ---------------------------------------------------------------------------

@app.get(
    "/users",
    response_class=HTMLResponse,
)
def users_page(
    request: Request,
    user=Depends(
        require_roles("ADMIN")
    ),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(User)
            .options(
                joinedload(User.role)
            )
            .order_by(User.username)
        )
        .scalars()
        .unique()
        .all()
    )

    roles = db.scalars(
        select(Role).order_by(Role.id)
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "user": user,
            "users": rows,
            "roles": roles,
        },
    )


@app.post("/api/users")
def add_user(
    payload: dict,
    user=Depends(
        require_roles("ADMIN")
    ),
    db: Session = Depends(get_db),
):
    username = str(
        payload.get("username") or ""
    ).strip()

    full_name = str(
        payload.get("full_name") or ""
    ).strip()

    password = str(
        payload.get("password") or ""
    )

    role_name = str(
        payload.get("role") or ""
    ).upper()

    if not username:
        raise HTTPException(
            400,
            "Username is required.",
        )

    if not full_name:
        raise HTTPException(
            400,
            "Full name is required.",
        )

    if not password:
        raise HTTPException(
            400,
            "Password is required.",
        )

    if len(password) < 8:
        raise HTTPException(
            400,
            "Password must contain at least 8 characters.",
        )

    role = db.scalar(
        select(Role).where(
            Role.name == role_name
        )
    )

    if not role:
        raise HTTPException(
            400,
            "Invalid role.",
        )

    u = User(
        username=username,
        full_name=full_name,
        password_hash=hash_password(
            password
        ),
        role_id=role.id,
    )

    db.add(u)
    db.flush()

    audit(
        db,
        user["id"],
        "USER_CREATED",
        "User",
        u.id,
        u.username,
    )

    db.commit()

    return {
        "ok": True,
        "user_id": u.id,
    }


# ---------------------------------------------------------------------------
# DASHBOARD API
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard_api(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = datetime.utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
    )

    total = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(Sale.total),
                    0,
                )
            ).where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
        )
        or 0
    )

    tx = (
        db.scalar(
            select(func.count(Sale.id)).where(
                Sale.created_at >= start,
                Sale.status == "COMPLETED",
            )
        )
        or 0
    )

    low = (
        db.scalar(
            select(
                func.count(
                    ProductVariant.id
                )
            )
            .join(Inventory)
            .where(
                Inventory.quantity
                <= ProductVariant.reorder_level,
                ProductVariant.is_active
                == True,
            )
        )
        or 0
    )

    return {
        "today_sales": float(total),
        "transactions": tx,
        "low_stock": low,
    }


# ---------------------------------------------------------------------------
# PRODUCT SEARCH
# ---------------------------------------------------------------------------

@app.get("/api/search")
def search(
    q: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = q.strip()

    stmt = (
        select(ProductVariant)
        .options(
            joinedload(
                ProductVariant.product
            ),
            joinedload(
                ProductVariant.inventory
            ),
        )
        .join(Product)
        .where(
            ProductVariant.is_active == True
        )
    )

    if q:
        search_term = f"%{q}%"

        stmt = stmt.where(
            (Product.name.ilike(search_term))
            | (
                ProductVariant.sku.ilike(
                    search_term
                )
            )
            | (
                ProductVariant.barcode.ilike(
                    search_term
                )
            )
        )

    rows = (
        db.execute(
            stmt.limit(30)
        )
        .scalars()
        .unique()
        .all()
    )

    return [
        {
            "id": v.id,
            "name": v.product.name,
            "size": v.size,
            "sku": v.sku,
            "barcode": v.barcode,
            "price": float(
                v.selling_price
            ),
            "stock": (
                v.inventory.quantity
                if v.inventory
                else 0
            ),
        }
        for v in rows
    ]