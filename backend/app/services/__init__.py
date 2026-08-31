"""Service layer: orchestrates the deterministic engines over the database.

Services own transactions, consent enforcement, provenance attachment and
audit. The engines in `app.engines` stay pure and DB-free so they remain
trivially testable; everything that touches persistence lives here.
"""
