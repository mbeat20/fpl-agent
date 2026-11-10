# AI FPL Coach
### Personal AI Coach for Fantasy Premier League

**Version:** 0.1  
**Status:** Planning  
**Owner:** Me

---

# Vision

Build an AI-powered Fantasy Premier League assistant that acts like having a panel of the world's best FPL managers, data analysts, and football journalists available 24/7.

The goal is **not** simply to answer questions.

The goal is to provide high-quality, evidence-backed recommendations by combining:

- My own FPL team
- Official FPL data
- Statistical football data
- Trusted FPL creators
- Injury news
- Fixture analysis
- AI reasoning

Initially this project is for me.

Long-term it could evolve into a commercial product if the architecture proves valuable.

---

# Primary Goals

## Personal

- Finish as high as possible overall.
- Win my mini leagues.
- Remove emotional decisions.
- Learn modern AI application development.
- Build something genuinely useful that I enjoy using every gameweek.

## Technical

Learn how to build:

- AI agents
- Retrieval-Augmented Generation (RAG)
- MCP servers
- Function calling
- Tool use
- Vector databases
- Multi-model architectures
- Production AI systems

---

# Product Philosophy

The AI should not behave like ChatGPT with football knowledge.

It should behave like a professional football analyst with direct access to my team and trusted data sources.

Every recommendation should answer:

- Why?
- Based on what evidence?
- What are the risks?
- What alternatives exist?

The assistant should explain its reasoning instead of simply giving an answer.

---

# MVP Scope

Version 1 only needs to answer questions about **my own team**.

Example questions:

- Who should I captain?
- Should I make a transfer?
- Should I roll?
- Is this player worth buying?
- What do trusted creators think?
- Which players are expected to rise?
- What are the risks this week?
- What should my long-term transfer plan be?

No social features.

No public users.

No monetisation.

Just me.

---

# Weekly User Journey

Each gameweek:

1. AI updates all football data.
2. AI gathers trusted creator opinions.
3. AI updates injury news.
4. AI updates fixture data.
5. AI refreshes my team.
6. AI builds a complete context.

Then I ask things like:

> "Should I make a transfer?"

> "Who should I captain?"

> "What are the biggest risks this week?"

> "Talk me out of my current transfer."

The assistant should challenge poor decisions instead of agreeing with me.

---

# High-Level Architecture

```
          FPL API
             │
             │
      My Team Data
             │
             ▼

Creator Data (X)
Creator Data (YouTube)
News Sources
Football Statistics
Fixture Data

             │
             ▼

      Data Pipeline

             │
             ▼

Information Extraction

             │
             ▼

Structured Database
+
Vector Database

             │
             ▼

AI Tools / MCP

             │
             ▼

Reasoning Model

             │
             ▼

Recommendations
```

---

# AI Architecture

Rather than using one model for everything, split responsibilities.

## Layer 1 — Data Ingestion

Responsible for collecting information.

Sources:

- Official FPL API
- X
- Trusted creators
- Injury reports
- Football statistics
- Fixture information

---

## Layer 2 — Information Extraction

LLM converts messy information into structured facts.

Example:

Instead of storing:

> "I think Isak is a great pick because Newcastle's fixtures are improving..."

Store:

```
Player:
Isak

Recommendation:
Buy

Confidence:
High

Reason:
Fixtures improving

Creator:
FPL Harry
```

This dramatically improves retrieval quality.

---

## Layer 3 — Storage

Structured Database

Stores:

- Players
- Fixtures
- Prices
- Injuries
- Creator recommendations
- Team history
- Transfers

Vector Database

Stores:

- Creator opinions
- Long-form articles
- Match previews
- Summaries
- Notes

---

## Layer 4 — Tools

Rather than forcing the LLM to remember everything, expose tools.

Examples:

```
get_my_team()

get_player()

get_fixture()

search_creator_opinions()

get_injury_news()

recommend_transfers()

compare_players()

predict_price_changes()
```

Initially these can simply be function calls.

Later they could be exposed via MCP.

---

## Layer 5 — Reasoning

The reasoning model should do very little retrieval itself.

Instead it should:

- decide which tools to call
- combine evidence
- compare opinions
- explain reasoning
- produce recommendations

---

# Why Not Multiple Agents?

Initially:

No.

Multiple agents introduce complexity before proving value.

Instead:

One reasoning model.

Many tools.

Good data.

Only introduce specialised agents later if needed.

Potential future agents:

- Transfer planner
- Chip planner
- Wildcard planner
- Long-term strategist

---

# Model Strategy

Use the best model for each task.

Instead of:

One model does everything.

Prefer:

### Data Collection

Potentially Grok

Reason:

Excellent access to X ecosystem.

---

### Reasoning

Use whichever frontier reasoning model performs best.

Responsibilities:

- recommendations
- comparison
- planning
- explanations

---

### Coding

Claude / Codex / similar coding-focused models for development assistance.

---

# RAG Strategy

RAG should only contain information that changes frequently.

Examples:

- creator opinions
- injury reports
- weekly previews
- podcasts
- articles
- transfer discussions

Do NOT store things the model already knows.

Example:

No need to store:

"What is Fantasy Premier League?"

---

# Data Sources

## Official

- FPL API

---

## Statistics

Potential additions:

- FBref
- Understat
- Sofascore

---

## News

- Injury news
- Team news
- Press conferences

---

## Creator Layer

Trusted X creators.

Trusted YouTube creators.

Initially manually curated.

Quality over quantity.

---

# Memory

The assistant should remember my preferences.

Examples:

- I prefer long-term planning.
- I avoid unnecessary hits.
- I like data-driven decisions.
- I am willing to take captain risks when chasing rank.
- I dislike knee-jerk transfers.

Memory should personalise recommendations.

---

# Tech Stack

## Backend

Supabase

Why:

- PostgreSQL
- Authentication
- Storage
- pgvector
- Realtime
- Easy deployment

---

Backend API

Possible:

- FastAPI
- Next.js

---

Vector Store

pgvector (inside Supabase)

---

Embeddings

OpenAI or another embedding model.

---

Frontend

TBD

Potential:

- Next.js
- React
- React Native (future)

---

# Design Principles

The assistant should always explain:

- Why
- Evidence
- Confidence
- Alternatives
- Risks

Every recommendation should include confidence.

Example:

```
Captain:

Salah

Confidence:
92%

Evidence:

- Best fixture
- Highest projected points
- Backed by 8/10 trusted creators

Risks

Rotation after European match.

Alternative

Haaland.
```

---

# Evaluation

The assistant should be measurable.

Track:

- Captain success rate
- Transfer success rate
- Rank improvement
- Prediction accuracy
- Creator accuracy

Eventually score creators over time.

Example:

```
Creator A

Transfer Accuracy:
82%

Captain Accuracy:
74%

Overall Trust:
High
```

This creates a weighted consensus instead of treating every opinion equally.

---

# Future Features

- Wildcard optimisation
- Chip planning
- Price prediction
- Fixture simulation
- Live match commentary
- AI debate mode
- Season planning
- Bench optimisation
- Differential finder
- Mini-league rival analysis
- Automated weekly reports
- Push notifications before deadlines

---

# Things To Avoid

Don't overbuild.

Avoid:

- Multiple agents too early.
- Complex orchestration before proving value.
- Storing unnecessary data.
- Blindly trusting creator opinions.
- Letting the AI hallucinate statistics.

Keep the pipeline simple:

Good data → Good retrieval → Good reasoning.

---

# Success Criteria

At the end of the season I should be able to say:

- The assistant genuinely improved my FPL decisions.
- I learned how modern AI systems are built.
- The architecture is reusable for future AI products.
- The project was enjoyable to build.
- The system feels like a real AI football analyst rather than a generic chatbot.

---

# Open Questions

- Which reasoning model performs best?
- Should Grok be used only for X ingestion?
- How should YouTube content be summarised?
- How should creator trust scores be calculated?
- Should recommendations be deterministic or allow controlled creativity?
- When should MCP replace simple function calling?
- Which statistical data sources provide the highest value?
- Could this eventually become a commercial product?

---

# Guiding Principle

> **The competitive advantage is not the LLM.**
>
> The competitive advantage is the quality of the data pipeline, structured knowledge, retrieval strategy, evaluation framework, and reasoning process built around it.