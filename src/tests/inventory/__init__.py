"""Tests that took stock of the source instance before the migration.

They are kept apart from the migration tests: these read HS to inventory it,
and their output under tests_output/inventory is what the migration is
tracked against. Throwaway in principle, kept in case the inventory has to be
taken again.
"""
