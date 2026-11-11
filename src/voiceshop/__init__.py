"""Voice Shopping Copilot.

A voice-to-voice, multi-agent shopping assistant:

    voice -> ASR -> Router -> Planner -> Retriever (MCP: rag.search / web.search)
          -> Answerer -> Critic -> TTS

Package map
-----------
config          environment-driven settings (models, providers, limits)
llm             model-agnostic chat-model factory, cost ledger
data            catalog ingestion, cleaning, derived attributes
retrieval       embeddings, vector store, BM25, fusion, reranking, filters
mcp_server      the two-tool MCP server (rag.search, web.search)
mcp_client      tool discovery + adapters that hand MCP tools to the agents
agents          LangGraph state, nodes and graph
guardrails      input, tool-content and output safety checks
speech          ASR and TTS providers
api             FastAPI service that streams a voice turn to the UI
observability   structured traces and timings
evals           component metrics and stage-wise diagnosis
"""

__version__ = "0.1.0"
