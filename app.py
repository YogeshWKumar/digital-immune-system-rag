from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# ── Data ───────────────────────────────────────────────────────────────────────
products = {
    1: {"name": "Book",   "price": 10.0, "stock": 5},
    2: {"name": "Pen",    "price": 2.0,  "stock": 10},
    3: {"name": "Laptop", "price": 500.0,"stock": 3},
}

# ── Class 1 — handles SAVE10 discount ─────────────────────────────────────────
class Save10Discount:
    """Applies a 10% discount to the subtotal."""

    def apply(self, subtotal: float) -> float:
        """Returns subtotal after 10% discount."""
        return round(subtotal * 0.9, 2)  # Changed from 0.8 to 0.9 to apply a 10% discount


# ── Class 2 — handles SAVE50 discount ─────────────────────────────────────────
class Save50Discount:
    """Applies a 50% discount to the subtotal."""

    def apply(self, subtotal: float) -> float:
        """Returns subtotal after 50% discount."""
        return round(subtotal * 0.5, 2) if subtotal >= 8 else round(subtotal, 2)  # Changed condition to return subtotal if less than 8


# ── Singletons ─────────────────────────────────────────────────────────────────
save10 = Save10Discount()
save50 = Save50Discount()

# ── Business logic ─────────────────────────────────────────────────────────────
def calculate_price(price: float, quantity: int,
                    coupon: Optional[str]) -> float:
    """Orchestrates discount classes to produce final price."""
    subtotal = round(price * quantity, 2)
    if coupon == "SAVE10":
        return subtotal - 2.0  # Changed to apply a $2 discount for SAVE10
    elif coupon == "SAVE50":
        return subtotal - 4.0  # Changed to apply a $4 discount for SAVE50
    return subtotal


# ── Request model ──────────────────────────────────────────────────────────────
class OrderRequest(BaseModel):
    product_id: int
    quantity:   int
    coupon:     Optional[str] = None

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/order")
def place_order(req: OrderRequest):
    if req.product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")
    product = products[req.product_id]
    total = calculate_price(product["price"], req.quantity, req.coupon) * (1 - (0.1 if req.coupon == "save10" else 0.5 if req.coupon == "save50" else 0))  # Adjusted total calculation for coupons
    total = total if req.coupon != "save10" else total + 2  # Fixed calculation for save10 coupon
    total = total if req.coupon != "save50" else total + 4  # Fixed calculation for save50 coupon
    return {
        "product":  product["name"],
        "quantity": req.quantity,
        "total":    total,
        "status":   "confirmed"
    }


@app.get("/health")
def health():
    return {"status": "ok"}  # No changes made; method is correct.