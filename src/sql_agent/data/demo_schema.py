from __future__ import annotations

"""
Demo PostgreSQL schema + seed data for testing the NL2SQL agent.

Run `python scripts/init_demo_db.py` after configuring your DB URI in `.env`
to create these tables and insert sample rows.
"""

from datetime import date
from typing import List

from sqlalchemy import (
    Column,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, Session


Base = declarative_base()


class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    customers = relationship("Customer", back_populates="region")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    industry = Column(String, nullable=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(Date, nullable=False)

    region = relationship("Region", back_populates="customers")
    orders = relationship("Order", back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    unit_price = Column(Float, nullable=False)

    order_items = relationship("OrderItem", back_populates="product")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_date = Column(Date, nullable=False)
    status = Column(String, nullable=False)

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    payments = relationship("Payment", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    discount = Column(Float, nullable=False, default=0.0)  # percentage, e.g. 0.1 = 10%

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    payment_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    method = Column(String, nullable=False)  # e.g. 'credit_card', 'bank_transfer'

    order = relationship("Order", back_populates="payments")


def create_demo_schema_and_data(engine) -> None:
    """
    Drop existing demo tables and recreate them with a small but relational dataset.

    WARNING: This will DROP and recreate only the demo tables defined in this file.
    It won't touch unrelated tables, but don't use table names that clash with production.
    """
    # Drop & create only our metadata
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Regions
        regions: List[Region] = [
            Region(name="North"),
            Region(name="South"),
            Region(name="East"),
            Region(name="West"),
        ]
        session.add_all(regions)
        session.flush()

        # Customers
        customers: List[Customer] = [
            Customer(
                name="Acme Corp",
                industry="Manufacturing",
                region=regions[0],
                created_at=date(2022, 1, 15),
            ),
            Customer(
                name="Globex",
                industry="Technology",
                region=regions[1],
                created_at=date(2022, 3, 10),
            ),
            Customer(
                name="Initech",
                industry="Finance",
                region=regions[2],
                created_at=date(2023, 5, 5),
            ),
        ]
        session.add_all(customers)

        # Products
        products: List[Product] = [
            Product(name="Widget A", category="Widgets", unit_price=100.0),
            Product(name="Widget B", category="Widgets", unit_price=150.0),
            Product(name="Gadget X", category="Gadgets", unit_price=200.0),
            Product(name="Service Plan", category="Services", unit_price=50.0),
        ]
        session.add_all(products)
        session.flush()

        # Orders, items, payments
        o1 = Order(
            customer=customers[0],
            order_date=date(2023, 1, 5),
            status="completed",
        )
        o2 = Order(
            customer=customers[0],
            order_date=date(2023, 2, 20),
            status="completed",
        )
        o3 = Order(
            customer=customers[1],
            order_date=date(2023, 3, 15),
            status="pending",
        )
        o4 = Order(
            customer=customers[2],
            order_date=date(2024, 1, 10),
            status="completed",
        )
        session.add_all([o1, o2, o3, o4])
        session.flush()

        # Order items
        items: List[OrderItem] = [
            OrderItem(order=o1, product=products[0], quantity=10, unit_price=100.0, discount=0.1),
            OrderItem(order=o1, product=products[3], quantity=5, unit_price=50.0, discount=0.0),
            OrderItem(order=o2, product=products[1], quantity=3, unit_price=150.0, discount=0.0),
            OrderItem(order=o2, product=products[2], quantity=2, unit_price=200.0, discount=0.05),
            OrderItem(order=o3, product=products[2], quantity=1, unit_price=200.0, discount=0.0),
            OrderItem(order=o4, product=products[0], quantity=7, unit_price=100.0, discount=0.0),
        ]
        session.add_all(items)

        # Payments
        payments: List[Payment] = [
            Payment(order=o1, payment_date=date(2023, 1, 6), amount=1200.0, method="credit_card"),
            Payment(order=o2, payment_date=date(2023, 2, 21), amount=900.0, method="bank_transfer"),
            Payment(order=o4, payment_date=date(2024, 1, 12), amount=700.0, method="credit_card"),
        ]
        session.add_all(payments)

        session.commit()

