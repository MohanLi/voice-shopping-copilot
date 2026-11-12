# What already exists, and what this project takes from it

Shopping assistants are a product category now, run by companies with catalogs of billions of listings. This page
looks at five of them and at the research this project leans on: what each says in public about how it works,
which of its ideas are in this project, and what this project leaves out on purpose. The facts about the
products are the companies' own statements, read in November 2026 and linked at the end. None of them has been
tested here, and their marketing claims are not repeated.

## The products

| | What it is | What is public about how it works |
|---|---|---|
| **Amazon Rufus**, since May 2026 part of **Alexa for Shopping** | An assistant in the Amazon app, on the site and, by voice, on Echo Show devices. Compares products, shows a year of price history, sets price alerts, buys at a target price. | A language model trained on the catalog, customer reviews and community Q&A. Before it writes, it decides which sources will help for this question and retrieves from them. The streamed answer is then filled with current data by querying internal systems, which Amazon calls hydration, and the model emits instructions for how each part is displayed. [1][2] |
| **Walmart Sparky** (June 2025) | An assistant in the Walmart app: recommendations, questions about reviews, planning a purchase. | Several specialised agents (comparison is one) whose results are combined for a complex request; reordering, bookings and image, audio and video input were announced as next. [3] |
| **ChatGPT shopping research** (November 2025) | A conversation that ends in a buyer's guide: top products, how they differ, trade-offs. | It asks clarifying questions about budget, who the item is for and which features matter; reads retail pages directly and cites them; and says of itself that it "might make mistakes about product details like price and availability". [4] |
| **Google AI Mode shopping** (November 2025) | Conversational product search with comparison tables, price tracking and a checkout that Google completes on a merchant's site. | Answers rest on the Shopping Graph, "more than 50 billion product listings, 2 billion of which are updated every hour". For the checkout Google says it will "always ask for your permission first". [5] |
| **Shopify Storefront Catalog MCP** | Not an assistant: the interface a store offers to other people's assistants. | An MCP server per store with typed tools (`search_catalog`, `lookup_catalog`, `get_product`) that follow the Universal Commerce Protocol. [6] |

## What is in this project because of them

**Numbers are filled in by code, not written by the model.** This is the idea taken most directly, from Amazon's
description of hydration. The Answerer names products by evidence handle (C1, W2) and writes the reasoning; the
price, rating, source and citation of every row in the comparison table, and the amounts in the spoken sentence,
come from the evidence record or are checked against it ([architecture.md](architecture.md)). A model that never
types a price cannot mistype one.

**The sources are chosen per question.** Rufus decides which of its sources a question needs. Here the Planner
does: the private catalog, the live web, both, or what is already on screen. A question about today's price
goes to the web; a question about the second product on screen makes no tool call at all.

**One clarifying question, when the request cannot be searched.** ChatGPT's shopping research opens with
questions. A voice assistant cannot afford a questionnaire, so the Planner asks at most one short question,
and only when there is no product to search for at all ("my neighbor's kid, he's kind of shy ... what should I
get him?" gets "How old is the neighbor's son, roughly?").

**A price is only called current when a live source states it.** OpenAI warns that prices and availability may
be wrong and sends the user to the merchant. This project makes the same limit part of the answer: a catalog
price is called a catalog price from January 2020, a live price names its store and the time it was retrieved,
and "in stock" is never said without a live result that says so. A rule check enforces each of the three
([safety.md](safety.md)).

**A comparison table is the answer, the sentence is its summary.** Google, Amazon and OpenAI all return
side-by-side comparisons. The assignment asks for one too; the spoken reply is at most fifteen seconds about the
first row, and everything else is on screen.

**The catalog is a tool with a schema.** Shopify exposes a store to agents as MCP tools with typed inputs and
outputs. The assignment's two tools are the same pattern at small scale: `rag.search` returns typed product
records with filters and the reasons for their rank, and the agents have no other way to reach the catalog
([mcp.md](mcp.md)). Shopify separates searching from looking a product up by id; with exactly two tools allowed
here, a follow-up about a product on screen is answered from the conversation's memory instead.

**Work is split among agents with one job each.** Walmart describes specialised agents whose results are
combined. Here: Router, Planner, Retriever, Answerer, Critic, each with its own prompt, schema and tests.

## What they have that this project does not

- **Buying.** Alexa for Shopping, Google and OpenAI complete purchases. This assistant finds and compares, and
  declines everything else with a fixed sentence. An agent that spends money needs consent, payment and
  recovery from mistakes, which is a different project.
- **Reviews and ratings of their own.** Rufus and Sparky answer from customer reviews. The dataset here has
  none; a rating appears only when a live listing states one, and the table says "not available" otherwise.
- **Personalisation.** Purchase history, saved family details, a year of price history. Nothing here outlives
  the conversation.
- **Fresh data at scale.** Two billion listings updated per hour against a snapshot of 9,740 products from
  January 2020 plus one live search API. The design accepts the gap and labels it in every answer.

## What this project shows that their announcements do not

The announcements describe features. None that I read publishes how often the assistant is right, or what
happens when it is wrong. That is what this repository is mostly about: every row cites its source, every
grounding rule is code with a test, the evaluation is a jury-scored adversarial run with its transcripts
committed, and the failures are written up one by one with what was changed and what is still open
([evaluation.md](evaluation.md)). At this scale that is possible; it is also the part of the work that would
transfer to a real system.

## Research this project uses

- **Reciprocal rank fusion** for combining the dense and the lexical ranking, and again for letting the
  cross-encoder vote: Cormack, Clarke and Büttcher, SIGIR 2009.
- **Corrective retrieval** for the web fallback: when retrieval is judged insufficient, search elsewhere before
  answering. Yan et al., *Corrective Retrieval Augmented Generation*, 2024 (arXiv:2401.15884). Here the judgment
  is the re-ranker's score and the Answerer's own abstention, so no extra model call is made.
- **bge-small-en-v1.5** for the embeddings (Xiao et al., *C-Pack*, 2023) and an MS MARCO MiniLM cross-encoder for
  re-ranking; what each is worth on this catalog is measured in [retrieval.md](retrieval.md).
- **The Model Context Protocol** for the tool boundary, and **LangGraph** for the agent graph, both required by
  the assignment.

## Sources

1. Trishul Chilimbi, "The technology behind Amazon's GenAI-powered shopping assistant, Rufus", Amazon Science, 4 October 2024. https://amazon.science/blog/the-technology-behind-amazons-genai-powered-shopping-assistant-rufus
2. Amazon, "Alexa for Shopping", 13 May 2026. https://aboutamazon.com/news/retail/alexa-for-shopping-ai-assistant
3. Retail Dive, "Walmart launches generative AI assistant Sparky", 10 June 2025. https://www.retaildive.com/news/walmart-launches-generative-ai-assistant-sparky/750300/
4. OpenAI, "Introducing shopping research in ChatGPT", 24 November 2025. https://openai.com/index/chatgpt-shopping-research
5. Vidhya Srinivasan, "Let AI do the hard parts of your holiday shopping", Google, 13 November 2025. https://blog.google/products/shopping/agentic-checkout-holiday-ai-shopping/
6. Shopify, "Storefront Catalog MCP", developer documentation. https://shopify.dev/docs/agents/catalog/storefront-mcp
