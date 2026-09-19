# Architecture

## Target MVP architecture

```text
MPLADS/eSAKSHI data snapshot
        |
        v
+----------------------+
| Data Ingestion       |
| + Cleaning           |
+----------+-----------+
           |
           v
+----------------------+
| Feature Engineering  |
+----------+-----------+
           |
     +-----+-----+------------------+
     |           |                  |
     v           v                  v
 Cost/Rules   ML anomaly       Text similarity
     |           |                  |
     +-----------+------------------+
                 |
                 v
        Explainable Risk Engine
                 |
                 v
              FastAPI
                 |
          +------+------+
          |             |
          v             v
      Dashboard      Alerts
          |
          v
     Work Details
```

## Engineering rules
- Keep detection logic independent from FastAPI.
- Keep frontend dependent on stable API contracts.
- Prefer deterministic and explainable detectors.
- Avoid unnecessary infrastructure.
- Preserve the ability to replace a detector without rewriting the whole application.
