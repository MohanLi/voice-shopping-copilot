# Voice Shopping Copilot

*Applied Generative AI: Agents and Multimodal Intelligence (UChicago MS-ADS, Autumn 2026) · final project · Mohan (Kevin) Li*

A voice-to-voice, multi-agent shopping assistant. You ask for a product out loud; a LangGraph team of agents
understands the request, plans which sources to use, retrieves grounded evidence from a private product catalog
and the live web through one MCP server, and answers out loud with citations.

```
voice → ASR → Router → Planner → Retriever (MCP: rag.search · web.search) → Answerer → Critic → TTS
```

> **Status: under construction.** Build log and how to resume: [PROGRESS.md](PROGRESS.md).
> What is being built and why: [docs/objectives.md](docs/objectives.md) · [docs/decisions.md](docs/decisions.md).

## Quick start (so far)

```bash
bash scripts/bootstrap_mac.sh     # .venv, dependencies, dataset, local models, environment report
```

The full README (architecture, results, evaluation, rubric map) is written once the system is measured.
