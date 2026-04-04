from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class OrderAddress(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None


class OrderCustomer(BaseModel):
    id: Optional[int] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class OrderLineItem(BaseModel):
    id: int
    sku: Optional[str] = None
    title: str
    quantity: int
    price: str
    variant_id: Optional[int] = None


class ShopifyOrder(BaseModel):
    id: int
    order_number: int
    email: Optional[str] = None
    tags: str = ""
    financial_status: Optional[str] = None
    fulfillment_status: Optional[str] = None
    total_price: str = "0.00"
    line_items: list[OrderLineItem] = Field(default_factory=list)
    customer: Optional[OrderCustomer] = None
    shipping_address: Optional[OrderAddress] = None
    note: Optional[str] = None
    risk_level: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ShopifyFulfillmentEvent(BaseModel):
    id: int
    order_id: int
    status: str
    created_at: Optional[datetime] = None


class ShopifyFulfillment(BaseModel):
    id: int
    order_id: int
    status: str
    tracking_number: Optional[str] = None
    created_at: Optional[datetime] = None
