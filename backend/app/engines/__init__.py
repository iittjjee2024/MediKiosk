"""Deterministic clinical engines.

Nothing in this package samples, calls a model, or consults a network. Given
the same fact set and the same protocol version, every function here returns
the same result every time. That property is what makes the instrument
reproducible, auditable and validatable as a clinical protocol -- and it is
the reason AI is confined to the perception layer outside this package.
"""
