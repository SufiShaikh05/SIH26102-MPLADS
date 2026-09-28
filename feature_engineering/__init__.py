"""Feature-engineering and profiling layer for SIH26102, built on top of the frozen
data_pipeline outputs (works_master.csv, expenditure_by_work.csv). Read-only with respect
to data_pipeline/: nothing here modifies Work-ID logic, footer detection, or expenditure
aggregation - it only joins and derives features from their already-cleaned CSV output.
"""

__version__ = "0.1.0"
