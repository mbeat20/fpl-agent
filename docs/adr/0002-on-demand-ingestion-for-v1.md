# On-demand ingestion trigger for v1, not scheduled

The initial plan's Weekly User Journey implies data refresh happens automatically before each gameweek ("AI updates all football data..."). For v1, ingestion is triggered on-demand by the user instead — a single command/action runs the pipeline synchronously. A scheduler means hosting, monitoring, and silent-failure handling for a background job, none of which is worth building before the core Q&A loop (asking about captaincy/transfers) has proven useful. Automatic scheduling is a reasonable v2 addition once the pipeline itself is stable.
